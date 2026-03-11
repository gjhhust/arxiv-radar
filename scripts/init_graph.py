"""
init_graph.py — Domain diffusion: BFS expansion from seed papers via Semantic Scholar.

First-run script to bootstrap the Paper Knowledge Network from seed arxiv IDs.

Algorithm:
  depth=0 → seed papers (fetched from arxiv + S2)
  depth=1 → all papers cited by seeds (references) + papers that cite seeds
  depth=2 → references of depth-1 papers (bounded by date/relevance filter)

Then runs baseline extraction on the expanded set to build COMPARES_WITH edges.
"""

from __future__ import annotations
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

from paper_db import PaperDB, EDGE_CITES
from semantic_scholar import (
    get_paper, get_references, get_citations,
    extract_arxiv_id, _arxiv_id_to_s2
)
from baseline_extractor import process_papers

logger = logging.getLogger(__name__)

# Default seed papers (landmark papers in the tracked domains)
DEFAULT_SEEDS = {
    "1D Image Tokenizer": [
        "2406.07550",   # TiTok
        "2310.05737",   # MAGVIT-v2
        "2406.06525",   # LlamaGen
        "2404.02905",   # VAR
        "2202.04200",   # MaskGIT
    ],
    "Unified Understanding & Generation": [
        "2408.01800",   # BAGEL
        "2510.11690",   # RAE
        "2309.11519",   # NextGPT
        "2401.12945",   # Unified-IO
        "2304.08485",   # Chameleon
    ],
}


def _s2_paper_to_local(s2_paper: dict, source: str = "s2_expansion") -> Optional[dict]:
    """Convert S2 paper dict to local paper dict format."""
    if not s2_paper or not s2_paper.get("title"):
        return None
    arxiv_id = extract_arxiv_id(s2_paper)
    if not arxiv_id:
        return None

    return {
        "id": arxiv_id,
        "s2_id": s2_paper.get("paperId", ""),
        "title": s2_paper.get("title", ""),
        "abstract": s2_paper.get("abstract", "") or "",
        "authors": [a.get("name", "") if isinstance(a, dict) else str(a)
                    for a in s2_paper.get("authors", [])],
        "date": s2_paper.get("publicationDate", ""),
        "best_score": 0.0,
        "paper_type": "方法文",
        "source": source,
        "s2_citation_count": s2_paper.get("citationCount", 0),
    }


def _is_recent_enough(paper_dict: dict, years_back: int = 3) -> bool:
    """Filter: only keep papers from the last N years."""
    date_str = paper_dict.get("date", "")
    if not date_str:
        return True  # Unknown date: keep
    try:
        pub_date = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        cutoff = date.today() - timedelta(days=365 * years_back)
        return pub_date >= cutoff
    except Exception:
        return True


def bfs_expand(
    seed_arxiv_ids: list[str],
    db: PaperDB,
    depth: int = 2,
    max_papers: int = 800,
    years_back: int = 3,
) -> dict:
    """
    BFS expansion from seed papers.

    Returns stats dict with counts.
    """
    visited_s2 = set()  # S2 IDs visited
    visited_arxiv = set()  # arxiv IDs visited
    queue = [(aid, 0) for aid in seed_arxiv_ids]
    papers_added = 0
    edges_added = 0

    logger.info(f"Starting BFS from {len(seed_arxiv_ids)} seeds, depth={depth}, max={max_papers}")

    while queue and papers_added < max_papers:
        arxiv_id, current_depth = queue.pop(0)

        if arxiv_id in visited_arxiv:
            continue

        # Fetch paper metadata from S2
        s2_data = get_paper(arxiv_id)
        if not s2_data:
            logger.warning(f"  S2: not found {arxiv_id}")
            continue

        s2_id = s2_data.get("paperId", "")
        if s2_id in visited_s2:
            continue

        visited_s2.add(s2_id)
        visited_arxiv.add(arxiv_id)

        # Save to DB
        local = _s2_paper_to_local(s2_data, source="s2_expansion" if current_depth > 0 else "seed")
        if local:
            local["id"] = arxiv_id  # use arxiv ID as primary key
            db.upsert_paper(local)
            papers_added += 1
            logger.info(f"  [d={current_depth}] Added: {s2_data['title'][:55]}... (#{papers_added})")

        if current_depth >= depth:
            continue

        # Fetch references (papers this paper cites)
        refs = get_references(arxiv_id, limit=50)
        for ref in refs:
            ref_arxiv = extract_arxiv_id(ref)
            if not ref_arxiv or ref_arxiv in visited_arxiv:
                continue
            # Only keep recent papers
            ref_local = _s2_paper_to_local(ref)
            if ref_local and _is_recent_enough(ref_local, years_back):
                db.add_edge(arxiv_id, ref_arxiv, EDGE_CITES,
                            metadata={"s2_id": ref.get("paperId", "")})
                edges_added += 1
                if current_depth + 1 < depth or len(queue) < 50:
                    queue.append((ref_arxiv, current_depth + 1))

        # Fetch citations (papers that cite this paper), only at depth 0-1
        if current_depth <= 1:
            cites = get_citations(arxiv_id, limit=30)
            for cite in cites:
                cite_arxiv = extract_arxiv_id(cite)
                if not cite_arxiv or cite_arxiv in visited_arxiv:
                    continue
                cite_local = _s2_paper_to_local(cite)
                if cite_local and _is_recent_enough(cite_local, years_back):
                    db.add_edge(cite_arxiv, arxiv_id, EDGE_CITES)
                    edges_added += 1
                    queue.append((cite_arxiv, current_depth + 1))

    stats = {
        "papers_added": papers_added,
        "edges_added": edges_added,
        "visited": len(visited_arxiv),
    }
    logger.info(f"BFS complete: {stats}")
    return stats


def run_domain_expansion(
    seeds: dict[str, list[str]] = None,
    db_path: str = None,
    depth: int = 2,
    max_papers: int = 800,
    years_back: int = 3,
    run_baseline: bool = True,
) -> dict:
    """
    Full domain expansion pipeline.

    Args:
        seeds: {domain_name: [arxiv_ids]} — defaults to DEFAULT_SEEDS
        db_path: path to SQLite DB
        depth: BFS depth (2 recommended)
        max_papers: max papers to fetch
        years_back: only include papers from last N years
        run_baseline: also run baseline extraction after expansion

    Returns:
        stats dict
    """
    seeds = seeds or DEFAULT_SEEDS
    db = PaperDB(db_path)

    all_seeds = []
    for domain, ids in seeds.items():
        logger.info(f"\n{'='*50}")
        logger.info(f"Domain: {domain} ({len(ids)} seeds)")
        for seed_id in ids:
            all_seeds.append(seed_id)

    # Merge all seeds for BFS (cross-domain links are valuable)
    logger.info(f"\nTotal seeds: {len(all_seeds)}")
    bfs_stats = bfs_expand(all_seeds, db, depth=depth,
                           max_papers=max_papers, years_back=years_back)

    total_stats = {"bfs": bfs_stats}

    # Run baseline extraction on all added papers
    if run_baseline:
        logger.info("\nRunning baseline extraction...")
        papers = db.search_papers(limit=max_papers + 100)
        p2_stats = process_papers(papers, db, use_llm=False)
        total_stats["baseline"] = p2_stats

    db_stats = db.stats()
    total_stats["db"] = db_stats
    logger.info(f"\nFinal DB stats: {json.dumps(db_stats, indent=2)}")
    return total_stats


# ─────────────────────── CLI ───────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")

    parser = argparse.ArgumentParser(description="Initialize paper knowledge graph via BFS expansion")
    parser.add_argument("--seeds", default=None, help="JSON file with seeds {domain: [arxiv_ids]}")
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--max-papers", type=int, default=500)
    parser.add_argument("--years-back", type=int, default=3)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--no-baseline", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Test with 2 seeds only")
    args = parser.parse_args()

    seeds = DEFAULT_SEEDS
    if args.seeds:
        seeds = json.loads(Path(args.seeds).read_text())

    if args.dry_run:
        seeds = {"1D Image Tokenizer": DEFAULT_SEEDS["1D Image Tokenizer"][:2]}
        args.max_papers = 20
        args.depth = 1

    stats = run_domain_expansion(
        seeds=seeds,
        db_path=args.db_path,
        depth=args.depth,
        max_papers=args.max_papers,
        years_back=args.years_back,
        run_baseline=not args.no_baseline,
    )
    print("\n=== Final Stats ===")
    print(json.dumps(stats, indent=2))
