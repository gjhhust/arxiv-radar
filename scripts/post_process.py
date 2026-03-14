"""
post_process.py — Sync v3 analysis results back into PaperDB.

Reads analysis result JSONs from data/cache/analysis_v3/ and:
  1. Updates paper fields: cn_oneliner, cn_abstract, contribution_type,
     editorial_note, why_read, keywords
  2. Writes EXTENDS edges for core_cite entries with role="extends"
  3. Stores method_variants via PaperDB.store_method_variants()
  4. Sets analysis_status="completed" + analysis_result_path

Note: CITES edges from core_cite are handled by fetch_queue.py (S2 data).
This module handles the LLM-derived metadata that supplements S2.

Usage:
    python post_process.py                              # Process all new results
    python post_process.py --paper 2406.07550            # Process one paper
    python post_process.py --db data/paper_network.db    # Custom DB path
    python post_process.py --dry-run                     # Preview without writing
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from paper_db import PaperDB, EDGE_EXTENDS, EDGE_CITES

logger = logging.getLogger(__name__)

DEFAULT_DB = ROOT / "data" / "paper_network.db"
DEFAULT_CACHE = ROOT / "data" / "cache" / "analysis_v3"


def _update_extra_fields(db: PaperDB, paper_id: str, fields: dict) -> None:
    """Direct SQL UPDATE for columns not covered by upsert_paper.

    Handles: keywords, editorial_note, why_read, and any other
    columns that exist in the papers table but not in upsert_paper's column list.
    """
    import sqlite3
    conn = db._connect()
    try:
        set_clauses = []
        values = []
        for col, val in fields.items():
            set_clauses.append(f"{col} = ?")
            values.append(val)
        set_clauses.append("updated_at = datetime('now')")
        values.append(paper_id)
        sql = f"UPDATE papers SET {', '.join(set_clauses)} WHERE id = ?"
        conn.execute(sql, values)
        conn.commit()
    finally:
        conn.close()


def _load_result(path: Path) -> Optional[dict]:
    """Load and validate an analysis result JSON."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"Cannot read {path.name}: {exc}")
        return None

    arxiv_id = data.get("arxiv_id", "").strip()
    if not arxiv_id:
        logger.debug(f"Skipping {path.name}: no arxiv_id")
        return None

    return data


def _role_to_edge_type(role: str) -> Optional[str]:
    """Map core_cite role to edge type. Returns None for roles not worth a new edge."""
    role = (role or "").lower().strip()
    if role in ("extends", "builds_on", "improves"):
        return EDGE_EXTENDS
    # "cites" edges are already handled by fetch_queue via S2 references.
    # Only return edge type for roles that add semantic meaning beyond CITES.
    return None


def process_result(
    db: PaperDB,
    result: dict,
    dry_run: bool = False,
) -> dict:
    """Process a single analysis result → update DB.

    Returns metrics dict.
    """
    paper_id = result["arxiv_id"]
    metrics = {
        "paper_id": paper_id,
        "fields_updated": False,
        "extends_edges": 0,
        "method_variants": 0,
        "status_set": False,
    }

    # ── 1. Update paper fields ────────────────────────────────────────────
    # Split fields into upsert-supported vs. extra columns
    # upsert_paper supports: cn_oneliner, cn_abstract, paper_type
    # keywords needs a direct SQL UPDATE (column exists but not in upsert_paper)
    # editorial_note, why_read are JSON-only — NOT in papers table schema
    upsert_fields = {}
    extra_fields = {}

    for key in ("cn_oneliner", "cn_abstract"):
        val = result.get(key, "").strip()
        if val:
            upsert_fields[key] = val

    ctype = result.get("contribution_type", "").strip()
    if ctype:
        upsert_fields["paper_type"] = ctype

    # keywords is a table column but not in upsert_paper's INSERT
    kw = result.get("keywords")
    if kw and isinstance(kw, list):
        extra_fields["keywords"] = json.dumps(kw, ensure_ascii=False)

    if upsert_fields and not dry_run:
        existing = db.get_paper(paper_id) or {}
        merged = {**existing, **upsert_fields, "id": paper_id}
        if "title" not in merged or not merged["title"]:
            merged["title"] = result.get("title", "")
        db.upsert_paper(merged)
        metrics["fields_updated"] = True
        logger.debug(f"Upserted fields for {paper_id}: {list(upsert_fields.keys())}")

    if extra_fields and not dry_run:
        _update_extra_fields(db, paper_id, extra_fields)
        metrics["fields_updated"] = True
        logger.debug(f"Updated extra fields for {paper_id}: {list(extra_fields.keys())}")

    # ── 2. Write EXTENDS edges from core_cite ─────────────────────────────
    for cc in result.get("core_cite", []):
        cc_id = (cc.get("arxiv_id") or cc.get("id") or "").strip()
        if not cc_id or cc_id.startswith("http"):
            continue
        edge_type = _role_to_edge_type(cc.get("role", ""))
        if edge_type and not dry_run:
            db.add_edge(
                paper_id, cc_id, edge_type,
                metadata={"source": "v3_analysis", "note": cc.get("note", "")},
            )
            metrics["extends_edges"] += 1
            logger.debug(f"Edge {paper_id} -[{edge_type}]-> {cc_id}")

    # ── 3. Store method_variants ──────────────────────────────────────────
    variants = result.get("method_variants", [])
    if variants and not dry_run:
        try:
            db.ensure_method_variants_table()
            n = db.store_method_variants(paper_id, variants)
            metrics["method_variants"] = n
        except Exception as exc:
            logger.warning(f"method_variants write failed for {paper_id}: {exc}")

    # ── 4. Set analysis_status = completed ────────────────────────────────
    result_path = (
        result.get("analysis_result_path")
        or result.get("_result_path", "")
    )
    if not dry_run:
        db.update_analysis_status(
            paper_id,
            analysis_status="completed",
            analysis_date=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            analysis_model=result.get("analysis_model"),
            analysis_session_id=result.get("analysis_session_id"),
            analysis_result_path=str(result_path) if result_path else None,
        )
        metrics["status_set"] = True

    return metrics


def run_post_process(
    db: PaperDB,
    cache_dir: Path = DEFAULT_CACHE,
    paper_id: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """Process all (or one) analysis result JSONs.

    Args:
        db:         PaperDB instance
        cache_dir:  Directory containing analysis_v3 JSON files
        paper_id:   If set, only process this paper
        dry_run:    If True, log what would happen without writing

    Returns:
        Summary stats dict.
    """
    summary = {
        "processed": 0,
        "updated": 0,
        "extends_edges": 0,
        "method_variants": 0,
        "skipped": 0,
        "errors": [],
    }

    if not cache_dir.is_dir():
        logger.warning(f"Cache dir not found: {cache_dir}")
        return summary

    json_files = sorted(cache_dir.glob("*.json"))
    if paper_id:
        json_files = [f for f in json_files if paper_id in f.stem]

    for path in json_files:
        result = _load_result(path)
        if not result:
            summary["skipped"] += 1
            continue

        # Skip if paper not in DB (it hasn't been fetched yet)
        if not db.get_paper(result["arxiv_id"]):
            logger.debug(f"Skipping {result['arxiv_id']}: not in DB (fetch first)")
            summary["skipped"] += 1
            continue

        # Skip if already post-processed (analysis_status == completed)
        if not dry_run:
            status = db.get_analysis_status(result["arxiv_id"])
            if status and status.get("analysis_status") == "completed":
                logger.debug(f"Skipping {result['arxiv_id']}: already completed")
                summary["skipped"] += 1
                continue

        try:
            metrics = process_result(db, result, dry_run=dry_run)
            summary["processed"] += 1
            if metrics["fields_updated"] or metrics["status_set"]:
                summary["updated"] += 1
            summary["extends_edges"] += metrics["extends_edges"]
            summary["method_variants"] += metrics["method_variants"]
        except Exception as exc:
            summary["errors"].append({"paper_id": result["arxiv_id"], "error": str(exc)})
            logger.error(f"Post-process failed for {result['arxiv_id']}: {exc}")

    mode = "DRY-RUN" if dry_run else "LIVE"
    logger.info(
        f"[{mode}] Post-process done: processed={summary['processed']}, "
        f"updated={summary['updated']}, extends_edges={summary['extends_edges']}, "
        f"method_variants={summary['method_variants']}, skipped={summary['skipped']}"
    )
    return summary


# ─────────────────── CLI ───────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Sync v3 analysis results → PaperDB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--db", default=str(DEFAULT_DB), help="Path to paper DB")
    p.add_argument("--cache", default=str(DEFAULT_CACHE),
                   help="Path to analysis_v3 cache directory")
    p.add_argument("--paper", default=None, metavar="ARXIV_ID",
                   help="Only process this paper's result")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview without writing to DB")
    p.add_argument("--verbose", action="store_true", help="Enable DEBUG logging")
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s — %(message)s",
    )

    db = PaperDB(args.db)
    summary = run_post_process(
        db,
        cache_dir=Path(args.cache),
        paper_id=args.paper,
        dry_run=args.dry_run,
    )

    print(f"\n{'🔍 DRY RUN' if args.dry_run else '✅ Post-process'} complete.")
    print(f"  Processed: {summary['processed']}")
    print(f"  Updated:   {summary['updated']}")
    print(f"  EXTENDS edges: {summary['extends_edges']}")
    print(f"  Method variants: {summary['method_variants']}")
    print(f"  Skipped:   {summary['skipped']}")
    if summary["errors"]:
        print(f"  Errors:    {len(summary['errors'])}")
        for e in summary["errors"]:
            print(f"    - {e['paper_id']}: {e['error']}")


if __name__ == "__main__":
    main()
