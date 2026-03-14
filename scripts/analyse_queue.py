"""
analyse_queue.py — Analyse Queue worker for arxiv-radar v3.

Processes a single analyse job:
  1. Look up paper title from DB
  2. Call analyse_paper() (paper_analyst_v3.py — the only LLM entry point)
  3. Extract core_cite → enqueue newly discovered papers into fetch queue
  4. Mark job done with result_path stored in metrics

Error handling:
  AnalyseError  — retryable (LLM timeout, spawn failure, JSON missing)
  AnalyseFatal  — non-retryable (paper not in DB, config disabled)
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Module-level import; allows unittest.mock.patch("analyse_queue.analyse_paper")
try:
    from paper_analyst_v3 import analyse_paper, AnalysisError, SpawnError
except ImportError:
    def analyse_paper(arxiv_id, title, **kwargs):  # type: ignore[misc]
        raise ImportError("paper_analyst_v3 not available")
    class AnalysisError(Exception): pass  # type: ignore[no-redef]
    class SpawnError(Exception): pass  # type: ignore[no-redef]


class AnalyseError(Exception):
    """Retryable analysis failure."""


class AnalyseFatal(Exception):
    """Non-retryable failure (paper missing from DB, config disabled)."""


# Priority for core_cite papers enqueued into fetch queue
CORE_CITE_FETCH_PRIORITY = 50


def _extract_core_cite_ids(result: dict) -> list[str]:
    """Pull arxiv IDs from core_cite list in analysis result."""
    arxiv_ids = []
    for item in result.get("core_cite", []):
        arxiv_id = item.get("arxiv_id") or item.get("id") or ""
        arxiv_id = arxiv_id.strip()
        if arxiv_id and not arxiv_id.startswith("http"):
            arxiv_ids.append(arxiv_id)
    return arxiv_ids


def process_analyse_job(
    db,
    job: dict,
    worker_id: str = "analyse-worker",
    config_path=None,
    spawn_executor: Optional[Callable] = None,
) -> dict:
    """Process a single analyse queue job.

    Args:
        db:              PaperDB instance
        job:             Job dict from lease_job()
        worker_id:       Identifier for this worker
        config_path:     Optional path to llm_analyse config YAML
        spawn_executor:  Optional override for spawning (used in tests)

    Returns:
        metrics dict: {
            "paper_id": str,
            "result_path": str,
            "core_cite_found": int,
            "fetch_enqueued": int,
            "latency_ms": int,
        }

    Raises:
        AnalyseError:  retryable (LLM, spawn, JSON)
        AnalyseFatal:  non-retryable (paper not in DB, config disabled)
    """
    paper_id = job["paper_id"]
    source = job.get("source", "seed")
    t0 = time.monotonic()

    metrics: dict = {
        "paper_id": paper_id,
        "result_path": "",
        "core_cite_found": 0,
        "fetch_enqueued": 0,
        "latency_ms": 0,
    }

    # ── Step 1: Resolve paper title from DB ─────────────────────────────
    paper_row = db.get_paper(paper_id)
    if not paper_row:
        raise AnalyseFatal(f"Paper {paper_id} not found in DB — cannot analyse")

    title = paper_row.get("title", "").strip()
    if not title:
        raise AnalyseFatal(f"Paper {paper_id} has empty title — cannot analyse")

    logger.info(f"[{worker_id}] Analysing: {paper_id} | {title[:60]}")

    # ── Step 2: Run LLM analysis ─────────────────────────────────────────
    try:
        result = analyse_paper(
            paper_id,
            title,
            db=db,
            config_path=config_path,
            spawn_executor=spawn_executor,
        )
    except AnalysisError as exc:
        err = str(exc)
        if "disabled" in err.lower():
            raise AnalyseFatal(err) from exc
        raise AnalyseError(err) from exc
    except SpawnError as exc:
        raise AnalyseError(f"Spawn failed: {exc}") from exc
    except Exception as exc:
        raise AnalyseError(f"Unexpected: {exc}") from exc

    # ── Step 3: Persist result path in metrics ───────────────────────────
    result_path = result.get("analysis_result_path") or result.get("_result_path", "")
    metrics["result_path"] = str(result_path)

    # ── Step 4: Extract core_cite → enqueue fetch jobs ───────────────────
    core_cite_ids = _extract_core_cite_ids(result)
    metrics["core_cite_found"] = len(core_cite_ids)

    for cc_id in core_cite_ids:
        db.enqueue_job(
            queue_type="fetch",
            paper_id=cc_id,
            source="core_cite",
            priority=CORE_CITE_FETCH_PRIORITY,
            dedupe_key=f"fetch:{cc_id}",
        )
        metrics["fetch_enqueued"] += 1

    if core_cite_ids:
        logger.info(
            f"[{worker_id}] core_cite enqueued {metrics['fetch_enqueued']} fetch jobs "
            f"for {paper_id}"
        )

    metrics["latency_ms"] = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"[{worker_id}] Done {paper_id}: core_cite={metrics['core_cite_found']}, "
        f"fetch_enqueued={metrics['fetch_enqueued']}, time={metrics['latency_ms']}ms"
    )
    return metrics


def run_analyse_batch(
    db,
    worker_id: str = "analyse-worker",
    max_jobs: int = 5,
    lease_timeout_sec: int = 1800,
    config_path=None,
    spawn_executor: Optional[Callable] = None,
) -> dict:
    """Process up to max_jobs analyse queue items in sequence.

    Handles ack/nack automatically. Returns summary stats.
    Note: max_jobs defaults to 5 (LLM calls are slow, ~60-300s each).
    """
    summary = {"processed": 0, "succeeded": 0, "retried": 0, "dead": 0, "errors": []}

    # Recover stale leased jobs
    recovered = db.recover_leased(lease_timeout_sec)
    if recovered:
        logger.info(f"[{worker_id}] Recovered {recovered} stale jobs")

    for _ in range(max_jobs):
        job = db.lease_job("analyse", worker_id, lease_timeout_sec)
        if not job:
            break

        summary["processed"] += 1
        try:
            metrics = process_analyse_job(
                db, job, worker_id,
                config_path=config_path,
                spawn_executor=spawn_executor,
            )
            db.ack_job(job["id"], run_metrics=metrics)
            summary["succeeded"] += 1

        except AnalyseFatal as exc:
            db.nack_job(job["id"], str(exc), "AnalyseFatal", max_retries=0)
            summary["dead"] += 1
            summary["errors"].append({"paper_id": job["paper_id"], "error": str(exc)})
            logger.error(f"[{worker_id}] Fatal {job['paper_id']}: {exc}")

        except AnalyseError as exc:
            status = db.nack_job(job["id"], str(exc), "AnalyseError")
            if status == "dead":
                summary["dead"] += 1
            else:
                summary["retried"] += 1
            summary["errors"].append({"paper_id": job["paper_id"], "error": str(exc)})
            logger.warning(f"[{worker_id}] Retryable {job['paper_id']}: {exc}")

        except Exception as exc:
            db.nack_job(job["id"], str(exc), type(exc).__name__)
            summary["retried"] += 1
            summary["errors"].append({"paper_id": job["paper_id"], "error": str(exc)})
            logger.error(f"[{worker_id}] Unexpected {job['paper_id']}: {exc}")

    return summary
