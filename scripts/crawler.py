"""
crawler.py — arxiv daily paper fetcher for arxiv-radar.

Fetches papers submitted/updated on a given date from specified categories.
Uses the arxiv Python library which handles rate limiting automatically.
"""

from __future__ import annotations
import time
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def _require_arxiv():
    try:
        import arxiv
        return arxiv
    except ImportError:
        raise ImportError(
            "arxiv library not installed. Run: pip install arxiv"
        )


def fetch_papers(
    categories: list[str] | None = None,
    target_date: date | None = None,
    max_results: int = 500,
) -> list[dict]:
    """
    Fetch papers from arxiv for the given categories and date.

    For dates within the last 2 days: uses recency-sorted query (fast).
    For older dates: uses submittedDate range query (handles historical data).

    Args:
        categories: List of arxiv category strings (e.g. ["cs.CV", "cs.LG"]).
                    Defaults to ["cs.CV", "cs.LG", "cs.AI"].
        target_date: Date to fetch (default: yesterday).
        max_results: Maximum number of results to fetch.

    Returns:
        List of paper dicts with keys:
            id, title, abstract, authors, date, categories, arxiv_url, pdf_url
    """
    arxiv = _require_arxiv()

    if categories is None:
        categories = ["cs.CV", "cs.LG", "cs.AI"]

    if target_date is None:
        target_date = date.today() - timedelta(days=1)

    days_ago = (date.today() - target_date).days
    logger.info(
        f"Fetching arxiv papers for {target_date} "
        f"({days_ago} days ago) in categories: {categories}"
    )

    # For historical dates (>2 days ago), use date-range query
    if days_ago > 2:
        return _fetch_papers_by_date_range(arxiv, categories, target_date, max_results)
    else:
        return _fetch_papers_recent(arxiv, categories, target_date, max_results)


def _fetch_papers_by_date_range(
    arxiv,
    categories: list[str],
    target_date: date,
    max_results: int,
) -> list[dict]:
    """Use submittedDate range query for historical dates."""
    cat_query = "(" + " OR ".join(f"cat:{c}" for c in categories) + ")"
    # Query window: target date ± 1 day to handle timezone edge cases
    d_start = target_date - timedelta(days=1)
    d_end = target_date + timedelta(days=1)
    date_query = (
        f"submittedDate:[{d_start.strftime('%Y%m%d')}0000 "
        f"TO {d_end.strftime('%Y%m%d')}2359]"
    )
    query = f"{cat_query} AND {date_query}"

    papers = []
    seen_ids = set()
    try:
        client = arxiv.Client(page_size=100, delay_seconds=3, num_retries=3)
        search = arxiv.Search(
            query=query,
            max_results=max_results,
            sort_by=arxiv.SortCriterion.SubmittedDate,
        )
        for result in client.results(search):
            submitted_date = result.published.date()
            # Strict filter: only keep target_date papers
            if abs((submitted_date - target_date).days) > 1:
                continue
            arxiv_id = result.entry_id.split("/abs/")[-1]
            if arxiv_id in seen_ids:
                continue
            seen_ids.add(arxiv_id)
            papers.append(_make_paper(result, submitted_date, arxiv_id))
    except Exception as e:
        logger.error(f"Error fetching arxiv papers (date-range): {e}")
        raise

    logger.info(f"Fetched {len(papers)} papers for {target_date} (date-range query)")
    return papers


def _fetch_papers_recent(
    arxiv,
    categories: list[str],
    target_date: date,
    max_results: int,
) -> list[dict]:
    """Fast recency-sorted fetch for recent dates (≤2 days ago)."""
    cat_query = " OR ".join(f"cat:{c}" for c in categories)
    papers = []
    seen_ids = set()
    try:
        client = arxiv.Client(page_size=100, delay_seconds=3, num_retries=3)
        search = arxiv.Search(
            query=cat_query,
            max_results=max_results,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )
        for result in client.results(search):
            submitted_date = result.published.date()
            delta = abs((submitted_date - target_date).days)
            if delta > 1:
                if (submitted_date - target_date).days < -1:
                    break
                continue
            arxiv_id = result.entry_id.split("/abs/")[-1]
            if arxiv_id in seen_ids:
                continue
            seen_ids.add(arxiv_id)
            papers.append(_make_paper(result, submitted_date, arxiv_id))
    except Exception as e:
        logger.error(f"Error fetching arxiv papers (recent): {e}")
        raise

    logger.info(f"Fetched {len(papers)} papers for {target_date} (recent query)")
    return papers


def _make_paper(result, submitted_date: date, arxiv_id: str) -> dict:
    return {
        "id": arxiv_id,
        "title": result.title.replace("\n", " ").strip(),
        "abstract": result.summary.replace("\n", " ").strip(),
        "authors": [a.name for a in result.authors],
        "date": str(submitted_date),
        "categories": result.categories,
        "primary_category": result.primary_category,
        "arxiv_url": f"https://arxiv.org/abs/{arxiv_id}",
        "pdf_url": result.pdf_url,
        "labels": [],
        "similarity_scores": {},
        "best_domain": None,
        "best_score": 0.0,
    }


def fetch_paper_by_id(arxiv_id: str) -> dict | None:
    """Fetch a single paper by its arxiv ID."""
    arxiv = _require_arxiv()

    try:
        client = arxiv.Client()
        search = arxiv.Search(id_list=[arxiv_id])
        results = list(client.results(search))
        if not results:
            return None
        result = results[0]
        return {
            "id": arxiv_id,
            "title": result.title.replace("\n", " ").strip(),
            "abstract": result.summary.replace("\n", " ").strip(),
            "authors": [a.name for a in result.authors],
            "date": str(result.published.date()),
            "categories": result.categories,
            "primary_category": result.primary_category,
            "arxiv_url": f"https://arxiv.org/abs/{arxiv_id}",
            "pdf_url": result.pdf_url,
            "labels": [],
            "similarity_scores": {},
            "best_domain": None,
            "best_score": 0.0,
        }
    except Exception as e:
        logger.error(f"Error fetching paper {arxiv_id}: {e}")
        return None


# ─────────────────────────── CLI / Standalone Test ───────────────────────────

if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)

    print("=== arxiv Crawler Standalone Test ===")
    print("Fetching up to 10 recent cs.CV papers...")

    papers = fetch_papers(
        categories=["cs.CV"],
        target_date=None,   # yesterday
        max_results=10,
    )

    print(f"\nFetched: {len(papers)} papers\n")
    for p in papers[:5]:
        print(f"  [{p['id']}] {p['title'][:70]}...")
        print(f"    Authors: {', '.join(p['authors'][:3])}")
        print(f"    Date: {p['date']} | Categories: {p['categories'][:2]}")
        print()

    if papers:
        print("✅ Crawler module working correctly")
    else:
        print("⚠️  No papers fetched - check date or network")
