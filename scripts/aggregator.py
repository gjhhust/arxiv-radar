"""
aggregator.py — Multi-day paper cache and aggregation for weekly/monthly reports.

Handles:
- Daily result caching (JSON) to avoid re-fetching
- Deduplication across days
- Score aggregation and trending analysis
"""

from __future__ import annotations
import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Cache dir: alongside the skill
CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"


def get_cache_path(target_date: date) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{target_date}.json"


def load_cached_day(target_date: date) -> dict | None:
    path = get_cache_path(target_date)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            logger.info(f"[cache hit] {target_date}: {len(data.get('papers', []))} papers")
            return data
        except Exception as e:
            logger.warning(f"Cache read error for {target_date}: {e}")
    return None


def save_cached_day(target_date: date, data: dict) -> None:
    path = get_cache_path(target_date)
    path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info(f"[cache saved] {target_date}")


def fetch_and_filter_day(
    target_date: date,
    config: dict,
    domains: list[dict],
    force_refresh: bool = False,
) -> dict:
    """
    Fetch, label, and filter papers for a single day.
    Uses cache if available.

    Returns dict with keys: date, papers, filter_result, stats
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from crawler import fetch_papers
    from labeler import label_papers
    from filter import filter_papers

    if not force_refresh:
        cached = load_cached_day(target_date)
        if cached:
            return cached

    logger.info(f"Fetching papers for {target_date}...")
    papers = fetch_papers(
        categories=config.get("arxiv_categories", ["cs.CV", "cs.LG", "cs.AI"]),
        target_date=target_date,
        max_results=config.get("max_papers_per_day", 500),
    )

    if papers:
        papers = label_papers(papers, config.get("vip_authors", []), config.get("orgs", []))
        filter_result = filter_papers(papers, config, domains)
    else:
        filter_result = {f"domain_{i}": [] for i in range(len(domains))}
        filter_result.update({"unmatched": [], "rejected_noise": [], "stats": {}})

    data = {
        "date": str(target_date),
        "total_crawled": len(papers),
        "papers": papers,
        "filter_result": filter_result,
        "stats": filter_result.get("stats", {}),
    }
    save_cached_day(target_date, data)
    return data


def aggregate_date_range(
    start_date: date,
    end_date: date,
    config: dict,
    domains: list[dict],
    force_refresh: bool = False,
) -> dict:
    """
    Aggregate papers across a date range.

    Returns:
    {
      "date_range": (start, end),
      "daily_results": [...],
      "all_papers": [...],          # deduplicated
      "domain_papers": {            # deduplicated per domain
        "domain_0": [...],
        "domain_1": [...],
      },
      "stats": {...},
    }
    """
    daily_results = []
    current = start_date
    while current <= end_date:
        day_data = fetch_and_filter_day(current, config, domains, force_refresh)
        daily_results.append(day_data)
        current += timedelta(days=1)

    # Deduplicate papers by arxiv ID
    seen_ids: set[str] = set()
    all_papers: list[dict] = []
    domain_papers: dict[str, list[dict]] = {
        f"domain_{i}": [] for i in range(len(domains))
    }
    domain_seen: dict[str, set[str]] = {
        f"domain_{i}": set() for i in range(len(domains))
    }

    total_crawled = 0
    total_noise = 0

    for day_data in daily_results:
        total_crawled += day_data.get("total_crawled", 0)
        total_noise += day_data.get("stats", {}).get("noise_rejected", 0)

        for paper in day_data.get("papers", []):
            pid = paper.get("id", "")
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                all_papers.append(paper)

        fr = day_data.get("filter_result", {})
        for key in domain_papers:
            for paper in fr.get(key, []):
                pid = paper.get("id", "")
                if pid and pid not in domain_seen[key]:
                    domain_seen[key].add(pid)
                    domain_papers[key].append(paper)

    # Sort each domain by score descending
    for key, papers in domain_papers.items():
        domain_idx = int(key.split("_")[1])
        domain_name = domains[domain_idx]["name"] if domain_idx < len(domains) else key
        domain_papers[key] = sorted(
            papers,
            key=lambda p: p.get("similarity_scores", {}).get(domain_name, 0),
            reverse=True,
        )

    total_relevant = sum(len(v) for v in domain_papers.values())

    return {
        "date_range": (start_date, end_date),
        "daily_results": daily_results,
        "all_papers": all_papers,
        "domain_papers": domain_papers,
        "stats": {
            "total_crawled": total_crawled,
            "unique_papers": len(all_papers),
            "total_noise_rejected": total_noise,
            "total_relevant": total_relevant,
            "per_domain": {
                domains[int(k.split("_")[1])]["name"]: len(v)
                for k, v in domain_papers.items()
                if int(k.split("_")[1]) < len(domains)
            },
            "days": len(daily_results),
        },
    }
