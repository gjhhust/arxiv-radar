"""
context_injector.py — Inject related paper context before LLM analysis.

For each "highlight" paper being analyzed, retrieves:
  1. Papers it cites (predecessors)
  2. Papers sharing the same baselines (peers on same method line)
  3. Semantically similar papers (SIMILAR_TO edges, from FAISS)

Then formats this into a compact context block injected into the LLM prompt,
enabling the model to produce analysis like:
  "This paper extends TiTok by... | Compared to CaTok which takes X approach,
   this paper instead uses Y approach | The COMPARES_WITH peers show..."
"""

from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Max tokens budget for context injection (keep prompt manageable)
MAX_CONTEXT_PAPERS = 5
MAX_ABSTRACT_CHARS = 200


def _truncate(text: str, n: int = MAX_ABSTRACT_CHARS) -> str:
    if not text or len(text) <= n:
        return text or ""
    return text[:n].rsplit(" ", 1)[0] + "..."


def _format_paper_snippet(paper: dict, relation: str = "") -> str:
    """Format a single paper as a compact context snippet."""
    title = paper.get("title", "")
    date_str = paper.get("date", "")[:7]  # YYYY-MM
    cn = paper.get("cn_oneliner") or paper.get("cn_abstract", "")[:100]
    snippet = cn or _truncate(paper.get("abstract", ""), 150)
    rel_label = f"[{relation}] " if relation else ""
    return f"• {rel_label}**{title}** ({date_str})\n  {snippet}"


def get_context_for_paper(paper_id: str, db, max_papers: int = MAX_CONTEXT_PAPERS) -> str:
    """
    Build a context block for a paper from the knowledge graph.

    Returns:
        Formatted markdown context string (empty if no context available)
    """
    if db is None:
        return ""

    context_papers = []
    seen_ids = {paper_id}

    # 1. Papers it cites (predecessors) — most important
    neighbors = db.get_neighbors(paper_id, edge_type="CITES", direction="out")
    for n in sorted(neighbors, key=lambda x: -x.get("weight", 1))[:2]:
        if n["neighbor_id"] not in seen_ids:
            p = db.get_paper(n["neighbor_id"])
            if p and p.get("title"):
                context_papers.append(("引用前驱", p))
                seen_ids.add(n["neighbor_id"])

    # 2. Papers sharing same baselines (method line peers)
    neighbors_cmp = db.get_neighbors(paper_id, edge_type="COMPARES_WITH", direction="both")
    for n in sorted(neighbors_cmp, key=lambda x: -x.get("weight", 1))[:2]:
        if n["neighbor_id"] not in seen_ids and len(context_papers) < max_papers:
            p = db.get_paper(n["neighbor_id"])
            if p and p.get("title"):
                context_papers.append(("同 Baseline 方法线", p))
                seen_ids.add(n["neighbor_id"])

    # 3. Papers it extends
    neighbors_ext = db.get_neighbors(paper_id, edge_type="EXTENDS", direction="out")
    for n in neighbors_ext[:1]:
        if n["neighbor_id"] not in seen_ids and len(context_papers) < max_papers:
            p = db.get_paper(n["neighbor_id"])
            if p and p.get("title"):
                context_papers.append(("直接继承", p))
                seen_ids.add(n["neighbor_id"])

    # 4. Similar papers (by embedding)
    neighbors_sim = db.get_neighbors(paper_id, edge_type="SIMILAR_TO", direction="both")
    for n in sorted(neighbors_sim, key=lambda x: -x.get("weight", 0))[:1]:
        if n["neighbor_id"] not in seen_ids and len(context_papers) < max_papers:
            p = db.get_paper(n["neighbor_id"])
            if p and p.get("title"):
                context_papers.append(("语义相似", p))
                seen_ids.add(n["neighbor_id"])

    if not context_papers:
        return ""

    lines = ["### 📚 领域背景（知识图谱注入）", ""]
    for relation, p in context_papers:
        lines.append(_format_paper_snippet(p, relation))
    lines.append("")
    return "\n".join(lines)


def build_enriched_prompt(paper: dict, base_prompt: str, db=None) -> str:
    """
    Inject knowledge graph context into an analysis prompt.

    Args:
        paper: paper dict being analyzed
        base_prompt: original LLM prompt
        db: PaperDB instance

    Returns:
        Enriched prompt with context prepended
    """
    context = get_context_for_paper(paper["id"], db)
    if not context:
        return base_prompt

    return f"{context}\n\n{base_prompt}"


def enrich_weekly_analysis(domain_papers: dict, db) -> dict:
    """
    For each highlight paper in domain_papers, add context block.
    Modifies papers in-place, adds 'graph_context' field.

    Args:
        domain_papers: {domain_key: [paper_dicts]}
        db: PaperDB instance

    Returns:
        Stats dict
    """
    enriched = 0
    for domain_key, papers in domain_papers.items():
        if not domain_key.startswith("domain_"):
            continue
        for paper in papers:
            ctx = get_context_for_paper(paper["id"], db)
            if ctx:
                paper["graph_context"] = ctx
                enriched += 1
    logger.info(f"Context injection: enriched {enriched} papers")
    return {"enriched": enriched}


def update_db_from_daily(daily_papers: list[dict], db) -> dict:
    """
    Incremental daily update: add new papers to DB and build edges.

    Args:
        daily_papers: new papers from today's crawl (already filtered)
        db: PaperDB instance

    Returns:
        stats dict
    """
    from semantic_scholar import get_paper as s2_get, get_references, extract_arxiv_id
    from baseline_extractor import process_papers
    import time

    stats = {"new_papers": 0, "s2_fetched": 0, "citation_edges": 0, "baseline_edges": 0}

    # 1. Upsert new papers
    for paper in daily_papers:
        if not db.get_paper(paper["id"]):
            db.upsert_paper(paper)
            stats["new_papers"] += 1

    logger.info(f"Daily update: {stats['new_papers']} new papers added to DB")

    # 2. For new papers, fetch S2 metadata and citation edges
    new_papers = [p for p in daily_papers if stats["new_papers"] > 0]
    for paper in new_papers[:20]:  # limit API calls per day
        s2_data = s2_get(paper["id"])
        if s2_data:
            stats["s2_fetched"] += 1
            # Update paper with S2 metadata
            updates = {
                **paper,
                "s2_id": s2_data.get("paperId", ""),
                "s2_citation_count": s2_data.get("citationCount", 0),
                "s2_reference_count": s2_data.get("referenceCount", 0),
            }
            db.upsert_paper(updates)

            # Build citation edges to papers already in DB
            refs = get_references(paper["id"], limit=30)
            for ref in refs:
                ref_arxiv = extract_arxiv_id(ref)
                if ref_arxiv and db.get_paper(ref_arxiv):
                    db.add_edge(paper["id"], ref_arxiv, "CITES")
                    stats["citation_edges"] += 1

        time.sleep(3.5)  # S2 rate limit

    # 3. Run baseline extraction on new papers
    if new_papers:
        p2_stats = process_papers(new_papers, db, use_llm=False)
        stats["baseline_edges"] = p2_stats.get("compares_edges", 0)

    logger.info(f"Daily DB update: {stats}")
    return stats


# ─────────────────────── CLI Test ───────────────────────

if __name__ == "__main__":
    import tempfile, sys
    sys.path.insert(0, str(Path(__file__).parent))
    logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")
    from paper_db import PaperDB, EDGE_CITES, EDGE_COMPARES_WITH

    print("=== Context Injector Test ===\n")

    db = PaperDB(tempfile.mktemp(suffix=".db"))

    # Insert some papers with relationships
    papers = [
        {"id": "2603.03276v1", "title": "Beyond Language Modeling", "best_score": 0.72,
         "cn_oneliner": "LeCun团队揭示多模态预训练关键规律", "date": "2026-03-03",
         "domain": "Unified", "paper_type": "方法文"},
        {"id": "2406.07550", "title": "TiTok: An Image is Worth 32 Tokens", "best_score": 0.90,
         "cn_oneliner": "32个token实现图像重建与生成", "date": "2024-06-11",
         "domain": "1D Tokenizer", "paper_type": "方法文"},
        {"id": "2603.06449v1", "title": "CaTok: Causal Image Tokenization", "best_score": 0.67,
         "cn_oneliner": "mean flow实现1D因果图像编码", "date": "2026-03-06",
         "domain": "1D Tokenizer", "paper_type": "方法文"},
    ]
    db.upsert_papers(papers)

    # Add edges
    db.add_edge("2603.03276v1", "2406.07550", EDGE_CITES)
    db.add_edge("2603.06449v1", "2406.07550", EDGE_COMPARES_WITH, weight=0.9)
    db.add_baselines("2603.03276v1", [{"name": "TiTok", "canonical": "titok"}])
    db.add_baselines("2603.06449v1", [{"name": "TiTok", "canonical": "titok"}])
    db.add_edge("2603.03276v1", "2603.06449v1", EDGE_COMPARES_WITH, weight=0.8)

    # Test context retrieval
    ctx = get_context_for_paper("2603.03276v1", db)
    print("Context for 'Beyond Language Modeling':")
    print(ctx if ctx else "(empty — add more edges for richer context)")

    ctx2 = get_context_for_paper("2603.06449v1", db)
    print("\nContext for 'CaTok':")
    print(ctx2 if ctx2 else "(empty)")

    # Test enriched prompt
    prompt = build_enriched_prompt(
        papers[0],
        "请分析这篇论文的核心贡献，并与相关工作对比。",
        db=db
    )
    print(f"\nEnriched prompt preview ({len(prompt)} chars):")
    print(prompt[:600])

    print("\n✅ Context injector test complete!")
