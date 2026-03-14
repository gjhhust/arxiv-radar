"""
fetch_queue.py — Fetch Queue worker for arxiv-radar v3.

Processes a single fetch job:
  1. Pull paper metadata from Semantic Scholar
  2. Upsert into papers table
  3. Fetch references (papers this paper cites) → write CITES edges
  4. Enqueue newly discovered papers into fetch queue (shallow stubs)
  5. Enqueue seed / core_cite papers into analyse queue

Rate limiting is handled by semantic_scholar.py.
Error handling: raises FetchError for retryable errors, FetchFatal for dead-letter.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Module-level imports allow unittest.mock.patch("fetch_queue.get_paper") to work.
# semantic_scholar.py must be on sys.path (guaranteed when running from scripts/).
try:
    from semantic_scholar import (
        get_paper,
        get_references,
        get_citations,
        extract_arxiv_id,
    )
except ImportError:
    # Stubs for environments without semantic_scholar (overridden by tests)
    def get_paper(arxiv_id):  # type: ignore[misc]
        raise ImportError("semantic_scholar not available")
    def get_references(arxiv_id, limit=100):  # type: ignore[misc]
        return []
    def get_citations(arxiv_id, limit=100):  # type: ignore[misc]
        return []
    def extract_arxiv_id(paper):  # type: ignore[misc]
        return None


class FetchError(Exception):
    """Retryable fetch failure (network, 429, S2 timeout)."""


class FetchFatal(Exception):
    """Non-retryable failure (bad paper_id format, persistent 404)."""


# Sources that should also be enqueued for LLM analysis
ANALYSE_SOURCES = {"seed", "core_cite", "manual"}

# Priority map for newly discovered papers added to fetch queue
FETCH_PRIORITY = {
    "seed": 10,
    "manual": 5,
    "core_cite": 50,
    "incremental": 30,
    "discovered": 80,   # papers discovered via reference expansion
}

ANALYSE_PRIORITY = {
    "seed": 10,
    "manual": 5,
    "core_cite": 50,
}


def _s2_to_db(s2: dict, arxiv_id: str, source: str) -> dict:
    """Convert Semantic Scholar paper dict to PaperDB upsert format."""
    pub_date = s2.get("publicationDate") or ""
    if not pub_date and s2.get("year"):
        pub_date = f"{s2['year']}-01-01"

    authors = s2.get("authors", [])
    if authors and isinstance(authors[0], dict):
        authors = [a.get("name", "") for a in authors]

    return {
        "id": arxiv_id,
        "s2_id": s2.get("paperId", ""),
        "title": s2.get("title", ""),
        "abstract": s2.get("abstract", ""),
        "authors": authors,
        "date": pub_date,
        "arxiv_url": f"https://arxiv.org/abs/{arxiv_id}",
        "s2_citation_count": s2.get("citationCount", 0),
        "s2_reference_count": s2.get("referenceCount", 0),
        "source": source,
    }


def process_fetch_job(
    db,
    job: dict,
    worker_id: str = "fetch-worker",
    fetch_citations: bool = False,
) -> dict:
    """Process a single fetch queue job.

    Args:
        db:              PaperDB instance
        job:             Job dict from lease_job()
        worker_id:       Identifier for this worker (for logging)
        fetch_citations: Also fetch incoming citations (expensive, default off)

    Returns:
        metrics dict: {
            "paper_id": str,
            "s2_found": bool,
            "references_fetched": int,
            "citations_fetched": int,
            "edges_written": int,
            "fetch_enqueued": int,
            "analyse_enqueued": int,
            "latency_ms": int,
        }

    Raises:
        FetchError:  retryable (network, 429)
        FetchFatal:  non-retryable (invalid ID, persistent failure)
    """
    paper_id = job["paper_id"]
    source = job.get("source", "incremental")
    t0 = time.monotonic()

    metrics = {
        "paper_id": paper_id,
        "s2_found": False,
        "references_fetched": 0,
        "citations_fetched": 0,
        "edges_written": 0,
        "fetch_enqueued": 0,
        "analyse_enqueued": 0,
        "latency_ms": 0,
    }

    logger.info(f"[{worker_id}] Fetching paper: {paper_id} (source={source})")

    # ── Step 1: Fetch paper metadata ──────────────────────────────────────
    try:
        s2_paper = get_paper(paper_id)
    except Exception as exc:
        raise FetchError(f"S2 get_paper failed: {exc}") from exc

    if s2_paper is None:
        # 404 from S2 — could be too new or wrong ID
        logger.warning(f"[{worker_id}] S2 404 for {paper_id}, skipping metadata write")
        # Don't raise fatal — paper may appear in S2 later; let retry handle it
        metrics["latency_ms"] = int((time.monotonic() - t0) * 1000)
        return metrics

    metrics["s2_found"] = True
    db_paper = _s2_to_db(s2_paper, paper_id, source)
    db.upsert_paper(db_paper)
    logger.info(f"[{worker_id}] Upserted: {db_paper['title'][:60]}")

    # ── Step 2: Fetch references (papers this paper cites) ────────────────
    try:
        refs = get_references(paper_id, limit=200)
    except Exception as exc:
        raise FetchError(f"S2 get_references failed: {exc}") from exc

    metrics["references_fetched"] = len(refs)

    for ref in refs:
        ref_arxiv = extract_arxiv_id(ref)
        if not ref_arxiv:
            continue  # Skip non-arxiv references

        # Write CITES edge: paper_id → ref_arxiv
        db.add_edge(
            paper_id, ref_arxiv, "CITES",
            metadata={"s2_id": ref.get("paperId", "")},
        )
        metrics["edges_written"] += 1

        # Stub-upsert the reference paper if not already in DB
        existing = db.get_paper(ref_arxiv)
        if not existing:
            stub = {
                "id": ref_arxiv,
                "s2_id": ref.get("paperId", ""),
                "title": ref.get("title", ""),
                "abstract": ref.get("abstract", ""),
                "authors": ref.get("authors", []),
                "s2_citation_count": ref.get("citationCount", 0),
                "source": "discovered",
            }
            db.upsert_paper(stub)

        # Enqueue for fetch (if not already processed)
        db.enqueue_job(
            queue_type="fetch",
            paper_id=ref_arxiv,
            source="discovered",
            priority=FETCH_PRIORITY["discovered"],
            dedupe_key=f"fetch:{ref_arxiv}",
        )
        metrics["fetch_enqueued"] += 1

    # ── Step 3: Optional incoming citations ───────────────────────────────
    if fetch_citations:
        try:
            cites = get_citations(paper_id, limit=100)
        except Exception as exc:
            logger.warning(f"[{worker_id}] get_citations failed (non-fatal): {exc}")
            cites = []

        metrics["citations_fetched"] = len(cites)
        for citer in cites:
            citer_arxiv = extract_arxiv_id(citer)
            if not citer_arxiv:
                continue
            db.add_edge(
                citer_arxiv, paper_id, "CITES",
                metadata={"s2_id": citer.get("paperId", "")},
            )
            metrics["edges_written"] += 1
            if not db.get_paper(citer_arxiv):
                db.upsert_paper({
                    "id": citer_arxiv,
                    "s2_id": citer.get("paperId", ""),
                    "title": citer.get("title", ""),
                    "authors": citer.get("authors", []),
                    "s2_citation_count": citer.get("citationCount", 0),
                    "source": "discovered",
                })
            db.enqueue_job(
                queue_type="fetch",
                paper_id=citer_arxiv,
                source="discovered",
                priority=FETCH_PRIORITY["discovered"],
                dedupe_key=f"fetch:{citer_arxiv}",
            )
            metrics["fetch_enqueued"] += 1

    # ── Step 4: Auto-enqueue into analyse queue for priority sources ───────
    if source in ANALYSE_SOURCES:
        db.enqueue_job(
            queue_type="analyse",
            paper_id=paper_id,
            source=source,
            priority=ANALYSE_PRIORITY.get(source, 50),
            dedupe_key=f"analyse:{paper_id}",
        )
        metrics["analyse_enqueued"] += 1
        logger.info(f"[{worker_id}] Enqueued for analysis: {paper_id}")

    metrics["latency_ms"] = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"[{worker_id}] Done {paper_id}: refs={metrics['references_fetched']}, "
        f"edges={metrics['edges_written']}, analyse={metrics['analyse_enqueued']}, "
        f"time={metrics['latency_ms']}ms"
    )
    return metrics


def run_fetch_batch(
    db,
    worker_id: str = "fetch-worker",
    max_jobs: int = 10,
    lease_timeout_sec: int = 1800,
) -> dict:
    """Process up to max_jobs fetch queue items in sequence.

    Handles ack/nack automatically. Returns summary stats.
    """
    summary = {"processed": 0, "succeeded": 0, "retried": 0, "dead": 0, "errors": []}

    # Recover any stale leased jobs first
    recovered = db.recover_leased(lease_timeout_sec)
    if recovered:
        logger.info(f"[{worker_id}] Recovered {recovered} stale jobs")

    for _ in range(max_jobs):
        job = db.lease_job("fetch", worker_id, lease_timeout_sec)
        if not job:
            break  # Queue empty

        summary["processed"] += 1
        try:
            metrics = process_fetch_job(db, job, worker_id)
            db.ack_job(job["id"], run_metrics=metrics)
            summary["succeeded"] += 1

        except FetchFatal as exc:
            status = db.nack_job(job["id"], str(exc), "FetchFatal", max_retries=0)
            summary["dead"] += 1
            summary["errors"].append({"paper_id": job["paper_id"], "error": str(exc)})
            logger.error(f"[{worker_id}] Fatal failure {job['paper_id']}: {exc}")

        except FetchError as exc:
            status = db.nack_job(job["id"], str(exc), "FetchError")
            if status == "dead":
                summary["dead"] += 1
            else:
                summary["retried"] += 1
            summary["errors"].append({"paper_id": job["paper_id"], "error": str(exc)})
            logger.warning(f"[{worker_id}] Retryable failure {job['paper_id']}: {exc}")

        except Exception as exc:
            status = db.nack_job(job["id"], str(exc), type(exc).__name__)
            summary["retried"] += 1
            summary["errors"].append({"paper_id": job["paper_id"], "error": str(exc)})
            logger.error(f"[{worker_id}] Unexpected error {job['paper_id']}: {exc}")

    return summary
