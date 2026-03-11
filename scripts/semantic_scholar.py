"""
semantic_scholar.py — Semantic Scholar API client for paper knowledge network.

Endpoints used:
  - GET /paper/{paper_id}           — paper metadata + citation/reference counts
  - GET /paper/{paper_id}/references — papers this paper cites
  - GET /paper/{paper_id}/citations  — papers that cite this paper
  - GET /paper/search               — keyword search

API: Free tier, 100 requests/5min. No API key required (but recommended).
Docs: https://api.semanticscholar.org/api-docs/

ID formats accepted:
  - arxiv:<id>  (e.g. "arxiv:2603.03276")
  - S2 paper ID (40-char hex)
  - DOI, MAG, ACL, PubMed IDs
"""

from __future__ import annotations
import json
import logging
import time
import urllib.request
import urllib.error
import urllib.parse
from typing import Any, Optional

logger = logging.getLogger(__name__)

BASE_URL = "https://api.semanticscholar.org/graph/v1"

# Fields to request
PAPER_FIELDS = "paperId,externalIds,title,abstract,year,authors,citationCount,referenceCount,publicationDate,url"
REF_FIELDS = "paperId,externalIds,title,abstract,year,authors,citationCount"

# Rate limiting
_last_request_time = 0.0
MIN_REQUEST_INTERVAL = 3.5  # seconds between requests (free tier is strict, ~100 req / 5 min)


def _rate_limit():
    """Enforce rate limiting between requests."""
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.time()


def _get(endpoint: str, params: dict = None) -> Optional[dict]:
    """Make a GET request to S2 API with rate limiting and error handling."""
    _rate_limit()

    url = f"{BASE_URL}/{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url, headers={
        "User-Agent": "arxiv-radar/2.0 (research-tracking-tool)",
        "Accept": "application/json",
    })

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            return data
    except urllib.error.HTTPError as e:
        if e.code == 404:
            logger.warning(f"S2 404: paper not found at {endpoint}")
            return None
        elif e.code == 429:
            logger.warning("S2 rate limited! Waiting 30s...")
            time.sleep(30)
            return _get(endpoint, params)  # retry once
        else:
            body = e.read().decode()[:200]
            logger.error(f"S2 HTTP {e.code}: {body}")
            return None
    except Exception as e:
        logger.error(f"S2 request error: {e}")
        return None


def _arxiv_id_to_s2(arxiv_id: str) -> str:
    """Convert arxiv ID to S2 query format.
    '2603.03276v1' → 'arxiv:2603.03276'  (strip version)
    """
    clean = arxiv_id.split("v")[0] if "v" in arxiv_id else arxiv_id
    if not clean.startswith("arxiv:"):
        clean = f"arxiv:{clean}"
    return clean


# ─────────────────────── Public API ───────────────────────

def get_paper(arxiv_id: str) -> Optional[dict]:
    """
    Get paper metadata from Semantic Scholar.

    Args:
        arxiv_id: arxiv ID (e.g. "2603.03276v1" or "2603.03276")

    Returns:
        dict with keys: paperId, title, abstract, authors, year, citationCount, etc.
        None if not found.
    """
    s2_id = _arxiv_id_to_s2(arxiv_id)
    data = _get(f"paper/{s2_id}", {"fields": PAPER_FIELDS})

    if data:
        # Normalize author format
        if "authors" in data:
            data["authors"] = [a.get("name", "") for a in data["authors"]]
        logger.info(f"S2 paper: {data.get('title', '')[:60]}... "
                     f"(citations={data.get('citationCount', 0)}, refs={data.get('referenceCount', 0)})")
    return data


def get_references(arxiv_id: str, limit: int = 100) -> list[dict]:
    """
    Get papers that this paper cites (its references).

    Returns:
        List of paper dicts (each has paperId, title, etc.)
    """
    s2_id = _arxiv_id_to_s2(arxiv_id)
    data = _get(f"paper/{s2_id}/references", {
        "fields": REF_FIELDS,
        "limit": min(limit, 1000),
    })

    if not data:
        return []

    papers = []
    for item in data.get("data", []):
        cited = item.get("citedPaper", {})
        if cited and cited.get("paperId"):
            if "authors" in cited:
                cited["authors"] = [a.get("name", "") for a in cited["authors"]]
            papers.append(cited)

    logger.info(f"S2 references for {arxiv_id}: {len(papers)} papers")
    return papers


def get_citations(arxiv_id: str, limit: int = 100) -> list[dict]:
    """
    Get papers that cite this paper.

    Returns:
        List of paper dicts (each has paperId, title, etc.)
    """
    s2_id = _arxiv_id_to_s2(arxiv_id)
    data = _get(f"paper/{s2_id}/citations", {
        "fields": REF_FIELDS,
        "limit": min(limit, 1000),
    })

    if not data:
        return []

    papers = []
    for item in data.get("data", []):
        citing = item.get("citingPaper", {})
        if citing and citing.get("paperId"):
            if "authors" in citing:
                citing["authors"] = [a.get("name", "") for a in citing["authors"]]
            papers.append(citing)

    logger.info(f"S2 citations for {arxiv_id}: {len(papers)} papers")
    return papers


def search_papers(query: str, limit: int = 20, year: str = None) -> list[dict]:
    """
    Search for papers by keyword.

    Args:
        query: search query string
        limit: max results (default 20, max 100)
        year: year filter (e.g. "2024-2026")

    Returns:
        List of paper dicts
    """
    params = {"query": query, "limit": min(limit, 100), "fields": PAPER_FIELDS}
    if year:
        params["year"] = year

    data = _get("paper/search", params)
    if not data:
        return []

    papers = []
    for item in data.get("data", []):
        if "authors" in item:
            item["authors"] = [a.get("name", "") for a in item["authors"]]
        papers.append(item)

    logger.info(f"S2 search '{query}': {len(papers)} results")
    return papers


def extract_arxiv_id(paper: dict) -> Optional[str]:
    """Extract arxiv ID from a Semantic Scholar paper dict."""
    ext = paper.get("externalIds", {})
    if ext and "ArXiv" in ext:
        return ext["ArXiv"]
    return None


def build_citation_edges(arxiv_id: str, db=None) -> dict:
    """
    Fetch references + citations for a paper and build edges.

    Args:
        arxiv_id: the paper to fetch citations for
        db: optional PaperDB instance to write edges to

    Returns:
        dict with stats: {"references": N, "citations": N, "edges_added": N}
    """
    stats = {"references": 0, "citations": 0, "edges_added": 0}

    # Get references (papers this paper cites)
    refs = get_references(arxiv_id)
    stats["references"] = len(refs)
    for ref in refs:
        ref_arxiv = extract_arxiv_id(ref)
        if ref_arxiv and db:
            ref_id = ref_arxiv if "v" in ref_arxiv else ref_arxiv
            db.add_edge(arxiv_id, ref_id, "CITES",
                        metadata={"s2_id": ref.get("paperId")})
            stats["edges_added"] += 1

    # Get citations (papers that cite this paper)
    cites = get_citations(arxiv_id)
    stats["citations"] = len(cites)
    for cite in cites:
        cite_arxiv = extract_arxiv_id(cite)
        if cite_arxiv and db:
            cite_id = cite_arxiv if "v" in cite_arxiv else cite_arxiv
            db.add_edge(cite_id, arxiv_id, "CITES",
                        metadata={"s2_id": cite.get("paperId")})
            stats["edges_added"] += 1

    return stats


# ─────────────────────── CLI Test ───────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")

    print("=== Semantic Scholar API Test ===\n")

    # Test 1: Get paper
    print("1. Get paper metadata: 2603.03276")
    p = get_paper("2603.03276v1")
    if p:
        print(f"   Title: {p['title'][:80]}")
        print(f"   Authors: {', '.join(p['authors'][:3])}...")
        print(f"   Citations: {p.get('citationCount', 0)}")
        print(f"   References: {p.get('referenceCount', 0)}")
        print(f"   S2 ID: {p.get('paperId', 'N/A')[:20]}...")
    else:
        print("   (not found — paper may be too recent for S2)")

    # Test 2: Get references
    print("\n2. Get references: 2406.07550 (TiTok)")
    refs = get_references("2406.07550")
    print(f"   Found {len(refs)} references")
    for r in refs[:3]:
        arxiv = extract_arxiv_id(r)
        print(f"   → {r['title'][:60]}... (arxiv:{arxiv or 'N/A'})")

    # Test 3: Get citations
    print("\n3. Get citations: 2406.07550 (TiTok)")
    cites = get_citations("2406.07550", limit=5)
    print(f"   Found {len(cites)} citing papers")
    for c in cites[:3]:
        arxiv = extract_arxiv_id(c)
        print(f"   ← {c['title'][:60]}... (arxiv:{arxiv or 'N/A'})")

    print("\n✅ Semantic Scholar API test complete!")
