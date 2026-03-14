"""
build_test_db.py — Bootstrap paper_network.db for G/H test

For each of 11 test papers:
  - ONE S2 API call: paper metadata + references in single request
  - Write paper → source="seed"
  - Write each reference → source="s2_expansion"
  - Write CITES edges
  - Add index on paper_edges(src_id, edge_type)

S2 rate limit: 8s between calls (from ARCHITECTURE.md)
"""
import json, logging, sys, time, urllib.request
from pathlib import Path
from typing import Optional

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))
from paper_db import PaperDB

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PAPERS = [
    "2406.07550", "2501.07730", "2503.08685", "2503.10772", "2504.08736",
    "2505.12053", "2505.21473", "2506.05289", "2507.08441", "2511.20565", "2601.01535",
]

S2_BASE = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = (
    "paperId,externalIds,title,abstract,year,authors,"
    "citationCount,referenceCount,publicationDate,"
    "references.paperId,references.externalIds,references.title,"
    "references.year,references.citationCount"
)
RATE_LIMIT = 8.0  # seconds between calls

def s2_get(arxiv_id: str) -> Optional[dict]:
    url = f"{S2_BASE}/paper/arXiv:{arxiv_id}?fields={S2_FIELDS}"
    req = urllib.request.Request(url, headers={"User-Agent": "arxiv-radar/3.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            logger.warning(f"  429 rate limit — sleeping 45s")
            time.sleep(45)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        logger.error(f"  HTTP {e.code} for {arxiv_id}")
        return None
    except Exception as e:
        logger.error(f"  Error fetching {arxiv_id}: {e}")
        return None

def extract_arxiv_id(paper: dict) -> Optional[str]:
    ext = paper.get("externalIds") or {}
    return ext.get("ArXiv") or ext.get("arxiv")

def s2_paper_to_local(data: dict, source: str) -> dict:
    authors = [a.get("name", "") for a in (data.get("authors") or [])]
    pub_date = data.get("publicationDate") or ""
    year = data.get("year") or ""
    date_str = pub_date[:10] if pub_date else (str(year) if year else "")
    arxiv_id = extract_arxiv_id(data)
    return {
        "id": arxiv_id or data.get("paperId", ""),
        "s2_id": data.get("paperId", ""),
        "title": data.get("title") or "",
        "abstract": data.get("abstract") or "",
        "authors": authors,
        "date": date_str,
        "arxiv_url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else "",
        "s2_citation_count": data.get("citationCount") or 0,
        "s2_reference_count": data.get("referenceCount") or 0,
        "source": source,
    }

def main():
    db_path = SKILL_DIR / "data" / "paper_network.db"
    db = PaperDB(db_path)
    logger.info(f"DB: {db_path}")

    total_seeds = 0
    total_refs = 0
    total_edges = 0

    for i, arxiv_id in enumerate(PAPERS):
        logger.info(f"\n[{i+1}/{len(PAPERS)}] Fetching {arxiv_id} ...")
        data = s2_get(arxiv_id)
        if not data:
            logger.warning(f"  ✗ skip {arxiv_id}")
            continue

        # Write seed paper
        seed = s2_paper_to_local(data, source="seed")
        seed["id"] = arxiv_id  # ensure arxiv_id is primary key
        db.upsert_paper(seed)
        total_seeds += 1
        logger.info(f"  ✓ seed: {seed['title'][:60]}  refs={data.get('referenceCount',0)}")

        # Write references
        refs = data.get("references") or []
        ref_count = 0
        edge_batch = []
        for ref in refs:
            ref_arxiv = extract_arxiv_id(ref)
            if not ref_arxiv or not ref.get("title"):
                continue
            ref_local = s2_paper_to_local(ref, source="s2_expansion")
            ref_local["id"] = ref_arxiv
            db.upsert_paper(ref_local)
            edge_batch.append((arxiv_id, ref_arxiv, "CITES", 1.0,
                               json.dumps({"s2_id": ref.get("paperId", "")})))
            ref_count += 1
        if edge_batch:
            db.add_edges_batch(edge_batch)
        total_refs += ref_count
        total_edges += len(edge_batch)
        logger.info(f"  ✓ refs written: {ref_count}  edges: {len(edge_batch)}")

        if i < len(PAPERS) - 1:
            logger.info(f"  (rate limit: sleeping {RATE_LIMIT}s)")
            time.sleep(RATE_LIMIT)

    # Add index for fast per-paper reference lookup
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_src ON paper_edges(src_id, edge_type)")
    conn.commit()
    conn.close()
    logger.info("\n✓ Index created: idx_edges_src")

    logger.info(f"""
╔══════════════════════════════╗
  DB Build Complete
  Seeds written:      {total_seeds:4d}
  References written: {total_refs:4d}
  CITES edges:        {total_edges:4d}
  DB path: {db_path}
╚══════════════════════════════╝""")

if __name__ == "__main__":
    main()
