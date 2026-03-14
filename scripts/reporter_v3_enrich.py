"""
reporter_v3_enrich.py — v3 analysis enrichment for arxiv-radar reports.

Provides utilities to:
  1. Enrich paper dicts with v3 analysis fields from PaperDB
     (cn_oneliner, paper_type, keywords, analysis status)
  2. Render a v3-enriched section for daily/weekly markdown reports
  3. Format core_cite chains from EXTENDS/CITES graph edges

This module is designed as an optional enrichment layer — it can be
called after base report generation without modifying reporter.py.

Usage:
    from reporter_v3_enrich import enrich_papers_with_v3, render_v3_section

    # Enrich paper dicts from DB
    enriched = enrich_papers_with_v3(papers, db)

    # Render a supplementary v3 section
    section = render_v3_section(enriched, title="🔬 Deep Analysis (v3)")
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
# Field extraction
# ─────────────────────────────────────────────────────────

def _safe_json_list(raw) -> list:
    """Parse a JSON list field; return empty list on failure."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            result = json.loads(raw)
            return result if isinstance(result, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def _read_analysis_json(result_path: str) -> Optional[dict]:
    """Read a v3 analysis result JSON file. Returns None on error."""
    if not result_path:
        return None
    path = Path(result_path)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug(f"Cannot read analysis result {result_path}: {exc}")
        return None


# ─────────────────────────────────────────────────────────
# Paper enrichment
# ─────────────────────────────────────────────────────────

def enrich_paper_with_v3(paper: dict, db=None) -> dict:
    """Enrich a single paper dict with v3 analysis fields.

    Pulls from DB (analysis_status, cn_oneliner, etc.) and optionally
    from the result JSON file for richer fields (core_cite, why_read).

    Args:
        paper:  Paper dict (must have "id" key)
        db:     Optional PaperDB instance for live DB lookups

    Returns:
        Enriched copy of the paper dict (original not mutated).
    """
    enriched = dict(paper)
    paper_id = paper.get("id", "")

    if not paper_id or db is None:
        return enriched

    # Pull DB record for analysis fields
    db_row = db.get_paper(paper_id)
    if not db_row:
        return enriched

    # Overlay v3 DB fields if not already present
    for field in (
        "cn_oneliner", "cn_abstract", "paper_type",
        "keywords", "analysis_status", "analysis_result_path",
        "analysis_model", "analysis_date",
    ):
        if db_row.get(field) and not enriched.get(field):
            enriched[field] = db_row[field]

    # If result JSON exists, pull richer fields
    result_path = db_row.get("analysis_result_path") or ""
    result_data = _read_analysis_json(result_path)
    if result_data:
        for field in ("why_read", "editorial_note", "contribution_type"):
            if result_data.get(field) and not enriched.get(field):
                enriched[field] = result_data[field]
        if result_data.get("core_cite") and not enriched.get("core_cite"):
            enriched["core_cite"] = result_data["core_cite"]

    return enriched


def enrich_papers_with_v3(papers: list[dict], db=None) -> list[dict]:
    """Enrich a list of papers with v3 analysis fields.

    Papers without v3 analysis (not yet analysed) are returned as-is.
    """
    return [enrich_paper_with_v3(p, db) for p in papers]


# ─────────────────────────────────────────────────────────
# Report rendering
# ─────────────────────────────────────────────────────────

def _format_core_cite(core_cite: list, max_items: int = 3) -> str:
    """Format core_cite list as compact inline markdown."""
    if not core_cite:
        return ""
    items = []
    for cc in core_cite[:max_items]:
        title = cc.get("title", "")
        arxiv_id = cc.get("arxiv_id") or cc.get("id", "")
        role = cc.get("role", "")
        if arxiv_id:
            url = f"https://arxiv.org/abs/{arxiv_id}"
            label = f"[{title}]({url})" if title else f"[{arxiv_id}]({url})"
        else:
            label = title or "?"
        role_tag = f" `{role}`" if role else ""
        items.append(f"{label}{role_tag}")
    suffix = f" + {len(core_cite) - max_items} more" if len(core_cite) > max_items else ""
    return " · ".join(items) + suffix


def _render_v3_paper_block(paper: dict, rank: int = 0) -> str:
    """Render a single paper as a v3-enriched markdown block."""
    title = paper.get("title", "Untitled")
    arxiv_url = paper.get("arxiv_url", "") or f"https://arxiv.org/abs/{paper.get('id', '')}"
    paper_type = paper.get("paper_type", "")
    cn_oneliner = paper.get("cn_oneliner", "")
    why_read = paper.get("why_read", "")
    editorial_note = paper.get("editorial_note", "")
    core_cite = paper.get("core_cite", [])
    keywords = _safe_json_list(paper.get("keywords"))
    analysis_status = paper.get("analysis_status", "")

    lines = []
    rank_prefix = f"**{rank}.** " if rank else ""

    lines.append(f"{rank_prefix}[**{title}**]({arxiv_url})")

    # Type badge + analysis status
    badges = []
    if paper_type:
        badges.append(f"`{paper_type}`")
    if analysis_status == "completed":
        badges.append("✅ v3")
    elif analysis_status == "analyzing":
        badges.append("⏳ analysing")
    if badges:
        lines.append(" ".join(badges))

    # Chinese oneliner (the most valuable v3 field)
    if cn_oneliner:
        lines.append(f"🇨🇳 {cn_oneliner}")

    # Why read
    if why_read:
        lines.append(f"💡 {why_read}")

    # Editorial note (methodology line)
    if editorial_note:
        lines.append(f"📝 {editorial_note}")

    # Core citations
    core_str = _format_core_cite(core_cite)
    if core_str:
        lines.append(f"🔗 核心引用: {core_str}")

    # Keywords
    if keywords:
        kw_str = " · ".join(f"`{k}`" for k in keywords[:6])
        lines.append(f"🏷️ {kw_str}")

    return "\n".join(lines)


def render_v3_section(
    papers: list[dict],
    title: str = "🔬 Deep Analysis (v3)",
    max_papers: int = 10,
    analysed_only: bool = False,
) -> str:
    """Render a v3-enriched report section for the given papers.

    Args:
        papers:        Enriched paper dicts (from enrich_papers_with_v3)
        title:         Section header
        max_papers:    Max papers to include
        analysed_only: If True, only show papers with analysis_status=completed

    Returns:
        Markdown string (empty if no papers to show)
    """
    if analysed_only:
        display = [p for p in papers if p.get("analysis_status") == "completed"]
    else:
        display = list(papers)

    # Sort: analysed first, then by best_score desc
    display.sort(key=lambda p: (
        0 if p.get("analysis_status") == "completed" else 1,
        -(p.get("best_score") or 0),
    ))
    display = display[:max_papers]

    if not display:
        return ""

    lines = ["", "---", "", f"## {title}", ""]

    for i, paper in enumerate(display, 1):
        block = _render_v3_paper_block(paper, rank=i)
        lines.append(block)
        lines.append("")

    return "\n".join(lines)


def append_v3_section_to_report(
    report: str,
    papers: list[dict],
    db=None,
    title: str = "🔬 Deep Analysis (v3)",
    max_papers: int = 10,
) -> str:
    """Enrich papers and append a v3 section to an existing report.

    Convenience wrapper: enrich + render + append in one call.

    Args:
        report:     Existing markdown report string
        papers:     Filtered/recommended paper dicts
        db:         PaperDB instance (optional; needed for live enrichment)
        title:      Section title
        max_papers: Max papers in v3 section

    Returns:
        Extended report string (original + v3 section appended).
    """
    enriched = enrich_papers_with_v3(papers, db=db)
    section = render_v3_section(enriched, title=title, max_papers=max_papers)
    if not section:
        return report
    return report + "\n" + section
