"""
main.py — Full pipeline runner for arxiv-radar.

Usage:
    python main.py                          # Run for yesterday
    python main.py --date 2024-06-01        # Run for specific date
    python main.py --config my_config.md    # Use custom config
    python main.py --dry-run                # Test without saving
    python main.py --max-papers 50          # Limit papers for testing
"""

from __future__ import annotations
import argparse
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Add scripts dir to path for local imports
SCRIPTS_DIR = Path(__file__).parent
SKILL_DIR = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from config_parser import parse_config
from crawler import fetch_papers
from filter import filter_papers
from labeler import label_papers
from recommender import recommend
from reporter import generate_report, save_report

logger = logging.getLogger(__name__)


def run_daily(
    config_path: str | Path | None = None,
    target_date: date | None = None,
    dry_run: bool = False,
    max_papers: int | None = None,
    verbose: bool = False,
) -> dict:
    """
    Run the full arxiv-radar daily pipeline.

    Returns:
        dict with keys: report, filter_result, recommendations, stats
    """
    log_level = logging.DEBUG if verbose else logging.INFO
    from log_config import setup_logging
    setup_logging(level=log_level)

    # ── 1. Config ──
    if config_path is None:
        config_path = SKILL_DIR / "config.template.md"
    config = parse_config(config_path)
    domains = config.get("domains", [])

    if not domains:
        logger.error("No domains configured! Check your config file.")
        sys.exit(1)

    if max_papers:
        config["max_papers_per_day"] = max_papers

    if target_date is None:
        target_date = date.today() - timedelta(days=1)

    logger.info(f"╔══════════════════════════════════════")
    logger.info(f"║ arxiv-radar daily run for {target_date}")
    logger.info(f"║ Domains: {[d['name'] for d in domains]}")
    logger.info(f"║ Categories: {config['arxiv_categories']}")
    logger.info(f"╚══════════════════════════════════════")

    # ── 2. Crawl ──
    logger.info("Step 1/5: Crawling arxiv...")
    papers = fetch_papers(
        categories=config["arxiv_categories"],
        target_date=target_date,
        max_results=config.get("max_papers_per_day", 500),
    )
    logger.info(f"  → {len(papers)} papers fetched")

    if not papers:
        logger.warning("No papers fetched! Check network or try a different date.")
        return {"report": "No papers found.", "stats": {}}

    # ── 3. Label first (labels used in recommendation scoring) ──
    logger.info("Step 2/5: Labeling papers...")
    papers = label_papers(
        papers,
        vip_list=config.get("vip_authors", []),
        orgs=config.get("orgs", []),
    )

    # ── 4. Semantic Filter ──
    logger.info("Step 3/5: Semantic filtering...")
    filter_result = filter_papers(papers, config, domains)
    stats = filter_result.get("stats", {})
    logger.info(
        f"  → Kept: {stats.get('total_filtered', 0)} papers "
        f"| Noise rejected: {stats.get('noise_rejected', 0)}"
    )

    # ── 5. Recommend ──
    logger.info("Step 4/5: Generating recommendations...")
    recommendations = recommend(filter_result, config, domains)
    for key, domain_result in recommendations.items():
        if key.startswith("domain_"):
            n = len(domain_result.get("recommendations", []))
            logger.info(f"  → {domain_result['name']}: {n} must-reads")

    # ── 6. Generate Report ──
    logger.info("Step 5/5: Generating report...")
    report = generate_report(
        all_papers=papers,
        filter_result=filter_result,
        recommendations=recommendations,
        domains=domains,
        config=config,
        report_date=str(target_date),
    )

    # ── 7. Save/Output ──
    if not dry_run:
        saved_path = save_report(report, config, str(target_date))
        if saved_path:
            logger.info(f"✅ Report saved to: {saved_path}")

        # ── 7b. Update knowledge graph with today's papers ──
        db_path = SKILL_DIR / "data" / "paper_network.db"
        if db_path.exists():
            try:
                from paper_db import PaperDB
                from context_injector import update_db_from_daily
                db = PaperDB(db_path)
                # Only add filtered (relevant) papers to DB
                relevant = filter_result.get("filtered_papers", [])
                if relevant:
                    db_stats = update_db_from_daily(relevant, db)
                    logger.info(f"📊 DB updated: {db_stats}")
            except Exception as e:
                logger.warning(f"DB update skipped: {e}")
    else:
        print("\n" + "═" * 60)
        print("DRY RUN — Report Preview:")
        print("═" * 60)
        print(report[:3000])
        if len(report) > 3000:
            print(f"\n... [{len(report) - 3000} more characters] ...")

    return {
        "report": report,
        "filter_result": filter_result,
        "recommendations": recommendations,
        "stats": stats,
        "papers_total": len(papers),
    }


# ─────────────────────────── CLI ───────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="arxiv-radar: Daily CV research paper tracker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                          Run for yesterday
  python main.py --date 2024-06-01        Run for specific date
  python main.py --config ~/myconfig.md   Use custom config
  python main.py --dry-run --max 20       Quick test run (20 papers)
  python main.py --verbose                Show debug output
        """,
    )
    parser.add_argument(
        "--config", "-c",
        default=None,
        help=f"Config file path (default: {SKILL_DIR}/config.template.md)",
    )
    parser.add_argument(
        "--date", "-d",
        default=None,
        help="Target date YYYY-MM-DD (default: yesterday)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print report to stdout without saving",
    )
    parser.add_argument(
        "--max", "-m",
        type=int, default=None,
        help="Max papers to fetch (for testing)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose logging",
    )

    args = parser.parse_args()

    target_date = None
    if args.date:
        from datetime import datetime
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()

    run_daily(
        config_path=args.config,
        target_date=target_date,
        dry_run=args.dry_run,
        max_papers=args.max,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
