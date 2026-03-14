"""
pipeline.py — arxiv-radar v3 Pipeline Entry Point

Coordinates Fetch → Analyse queue processing in sequence.

Usage:
    # Seed papers and run pipeline:
    python pipeline.py --seed 2501.00001 2501.00002 --max-fetch 20 --max-analyse 5

    # Dry run (only shows queue stats, no processing):
    python pipeline.py --seed 2501.00001 --dry-run

    # Resume (process whatever is already queued):
    python pipeline.py --max-fetch 20 --max-analyse 5
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from paper_db import PaperDB
from fetch_queue import run_fetch_batch
from analyse_queue import run_analyse_batch

logger = logging.getLogger(__name__)

DEFAULT_DB = ROOT / "data" / "paper_network.db"


def seed_papers(
    db: PaperDB,
    arxiv_ids: list[str],
    source: str = "seed",
    priority: int = 10,
) -> int:
    """Enqueue a list of arxiv IDs into the fetch queue.

    Returns number of newly enqueued jobs (skips already-queued).
    """
    enqueued = 0
    for arxiv_id in arxiv_ids:
        arxiv_id = arxiv_id.strip()
        if not arxiv_id:
            continue
        added = db.enqueue_job(
            queue_type="fetch",
            paper_id=arxiv_id,
            source=source,
            priority=priority,
            dedupe_key=f"fetch:{arxiv_id}",
        )
        if added:
            enqueued += 1
            logger.info(f"Seeded: {arxiv_id}")
        else:
            logger.debug(f"Already queued: {arxiv_id}")
    return enqueued


def run_pipeline(
    db: PaperDB,
    max_fetch: int = 20,
    max_analyse: int = 5,
    worker_id: str = "pipeline",
    config_path=None,
    spawn_executor=None,
) -> dict:
    """Run one cycle of fetch → analyse.

    Args:
        db:           PaperDB instance
        max_fetch:    Max fetch jobs to process this cycle
        max_analyse:  Max analyse jobs to process this cycle
        worker_id:    Worker identifier for logs
        config_path:  Path to llm_analyse config YAML (analyse worker)
        spawn_executor: Override for spawning (used in tests)

    Returns:
        Combined summary stats dict.
    """
    logger.info(f"[{worker_id}] Pipeline start — max_fetch={max_fetch}, max_analyse={max_analyse}")

    # Phase A: Fetch
    fetch_summary = run_fetch_batch(db, worker_id=f"{worker_id}/fetch", max_jobs=max_fetch)
    logger.info(
        f"[{worker_id}] Fetch done: processed={fetch_summary['processed']}, "
        f"ok={fetch_summary['succeeded']}, retry={fetch_summary['retried']}, "
        f"dead={fetch_summary['dead']}"
    )

    # Phase B: Analyse
    analyse_summary = run_analyse_batch(
        db,
        worker_id=f"{worker_id}/analyse",
        max_jobs=max_analyse,
        config_path=config_path,
        spawn_executor=spawn_executor,
    )
    logger.info(
        f"[{worker_id}] Analyse done: processed={analyse_summary['processed']}, "
        f"ok={analyse_summary['succeeded']}, retry={analyse_summary['retried']}, "
        f"dead={analyse_summary['dead']}"
    )

    return {
        "fetch": fetch_summary,
        "analyse": analyse_summary,
        "queue_stats": db.get_queue_stats(),
    }


# ─────────────────── CLI ───────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="arxiv-radar v3 pipeline: fetch + analyse",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--seed", nargs="+", metavar="ARXIV_ID",
                   help="Seed arxiv IDs to add to fetch queue before running")
    p.add_argument("--seed-source", default="seed",
                   choices=["seed", "manual", "core_cite", "incremental"],
                   help="Source label for seeded papers (default: seed)")
    p.add_argument("--max-fetch", type=int, default=20,
                   help="Max fetch jobs per cycle (default: 20)")
    p.add_argument("--max-analyse", type=int, default=5,
                   help="Max analyse jobs per cycle (default: 5)")
    p.add_argument("--db", default=str(DEFAULT_DB), help="Path to paper DB")
    p.add_argument("--config", default=None, help="Path to llm_analyse config YAML")
    p.add_argument("--dry-run", action="store_true",
                   help="Show queue stats only, do not process jobs")
    p.add_argument("--verbose", action="store_true", help="Enable DEBUG logging")
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s — %(message)s",
    )

    db = PaperDB(args.db)

    if args.seed:
        n = seed_papers(db, args.seed, source=args.seed_source)
        print(f"Seeded {n} new paper(s) into fetch queue.")

    if args.dry_run:
        stats = db.get_queue_stats()
        print("\n📊 Queue stats (dry-run):")
        for qtype, counts in stats.items():
            print(f"  {qtype}: {counts}")
        return

    result = run_pipeline(
        db,
        max_fetch=args.max_fetch,
        max_analyse=args.max_analyse,
        config_path=args.config,
    )

    print("\n✅ Pipeline complete.")
    print(f"  Fetch:   processed={result['fetch']['processed']}, "
          f"ok={result['fetch']['succeeded']}, dead={result['fetch']['dead']}")
    print(f"  Analyse: processed={result['analyse']['processed']}, "
          f"ok={result['analyse']['succeeded']}, dead={result['analyse']['dead']}")
    print(f"\n📊 Remaining queue:")
    for qtype, counts in result["queue_stats"].items():
        print(f"  {qtype}: {counts}")


if __name__ == "__main__":
    main()
