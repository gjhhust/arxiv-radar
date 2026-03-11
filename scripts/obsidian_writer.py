"""
obsidian_writer.py — Generate Obsidian notes for each paper with wiki-links.

Each paper gets a .md file with:
  - YAML frontmatter (id, title, date, domain, score, paper_type, baselines)
  - Chinese abstract + one-liner
  - Related papers as [[wiki-links]]
  - Method lineage section (extends / compared_with)
  - Reading notes template
"""

from __future__ import annotations
import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _sanitize_filename(title: str, max_len: int = 80) -> str:
    """Convert paper title to a safe filename."""
    # Remove special characters, keep alphanumeric and spaces/hyphens
    safe = re.sub(r'[^\w\s\-]', '', title)
    safe = re.sub(r'\s+', ' ', safe).strip()
    return safe[:max_len]


def _format_authors(authors_json: str, n: int = 3) -> str:
    """Format authors from JSON string."""
    try:
        authors = json.loads(authors_json) if isinstance(authors_json, str) else authors_json
        if len(authors) <= n:
            return ", ".join(authors)
        return ", ".join(authors[:n]) + " et al."
    except Exception:
        return str(authors_json)[:100]


def _get_baselines_for_paper(paper_id: str, db) -> list[str]:
    """Get canonical baseline names for a paper."""
    import sqlite3
    try:
        conn = sqlite3.connect(str(db.db_path))
        rows = conn.execute(
            "SELECT DISTINCT canonical_name FROM baselines WHERE paper_id = ? AND canonical_name != ''",
            (paper_id,)
        ).fetchall()
        conn.close()
        return [r[0] for r in rows if r[0]]
    except Exception:
        return []


def _get_related_papers(paper_id: str, db, max_per_type: int = 5) -> dict:
    """Get related papers grouped by edge type."""
    neighbors = db.get_neighbors(paper_id, direction="out")
    by_type = {}
    for n in neighbors:
        etype = n["edge_type"]
        if etype not in by_type:
            by_type[etype] = []
        if len(by_type[etype]) < max_per_type:
            neighbor_paper = db.get_paper(n["neighbor_id"])
            if neighbor_paper:
                by_type[etype].append({
                    "id": n["neighbor_id"],
                    "title": neighbor_paper["title"],
                    "weight": n["weight"],
                })
    return by_type


def paper_to_obsidian_note(paper: dict, db=None) -> str:
    """Generate Obsidian note markdown for a single paper."""
    pid = paper["id"]
    title = paper.get("title", "Unknown")
    abstract = paper.get("abstract", "")
    cn_abstract = paper.get("cn_abstract", "")
    cn_oneliner = paper.get("cn_oneliner", "")
    domain = paper.get("domain", "")
    score = paper.get("best_score", 0)
    paper_type = paper.get("paper_type", "方法文")
    date_str = paper.get("date", "")[:10] if paper.get("date") else ""
    authors_raw = paper.get("authors", "[]")
    authors = _format_authors(authors_raw)

    # Frontmatter tags
    type_tag = paper_type.replace(" ", "-")
    domain_tag = domain.replace(" ", "-").replace("&", "and")
    labels_raw = paper.get("labels", "[]")
    try:
        labels = json.loads(labels_raw) if isinstance(labels_raw, str) else labels_raw
    except Exception:
        labels = []
    # Clean labels for YAML
    label_tags = [re.sub(r'[^\w\-/]', '', l.replace(" ", "-")) for l in labels if l]

    # Get baselines and relations
    baselines = _get_baselines_for_paper(pid, db) if db else []
    related = _get_related_papers(pid, db) if db else {}

    # Build note
    lines = []

    # YAML Frontmatter
    lines.append("---")
    lines.append(f"id: {pid}")
    lines.append(f'title: "{title.replace(chr(34), chr(39))}"')
    lines.append(f"date: {date_str}")
    lines.append(f"domain: {domain_tag}")
    lines.append(f"score: {int(score * 100)}")
    lines.append(f"paper_type: {paper_type}")
    if baselines:
        lines.append(f"baselines: [{', '.join(baselines[:6])}]")
    lines.append(f"status: unread")
    lines.append(f"tags:")
    lines.append(f"  - 科研追踪")
    lines.append(f"  - {domain_tag}")
    lines.append(f"  - {type_tag}")
    for lt in label_tags[:3]:
        if lt:
            lines.append(f"  - {lt}")
    lines.append(f"arxiv_url: https://arxiv.org/abs/{pid}")
    lines.append(f"created: {date.today().isoformat()}")
    lines.append("---")
    lines.append("")

    # Title + metadata
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"👤 **{authors}** | 📅 {date_str} | 📈 相关性 **{int(score*100)}** | 🏷️ {paper_type}")
    lines.append(f"🔗 [arXiv:{pid}](https://arxiv.org/abs/{pid})")
    lines.append("")

    # Chinese summary
    if cn_oneliner:
        lines.append(f"> 💡 **{cn_oneliner}**")
        lines.append("")
    if cn_abstract:
        lines.append("## 中文摘要")
        lines.append("")
        lines.append(cn_abstract)
        lines.append("")
    elif abstract:
        lines.append("## Abstract")
        lines.append("")
        lines.append(f"> {abstract[:600]}{'...' if len(abstract) > 600 else ''}")
        lines.append("")

    # Method lineage
    if baselines or related.get("EXTENDS") or related.get("COMPARES_WITH"):
        lines.append("## 方法线索")
        lines.append("")
        if related.get("EXTENDS"):
            ext_links = " · ".join(
                f"[[{_sanitize_filename(r['title'])}]]"
                for r in related["EXTENDS"]
            )
            lines.append(f"**继承自**: {ext_links}")
        if baselines:
            baseline_links = " · ".join(f"[[{b}]]" for b in baselines[:6])
            lines.append(f"**对比 Baseline**: {baseline_links}")
        if related.get("COMPARES_WITH"):
            peer_links = " · ".join(
                f"[[{_sanitize_filename(r['title'])}]]"
                for r in related["COMPARES_WITH"][:3]
            )
            lines.append(f"**同线路论文**: {peer_links}")
        if related.get("CITES"):
            cite_links = " · ".join(
                f"[[{_sanitize_filename(r['title'])}]]"
                for r in related["CITES"][:3]
            )
            lines.append(f"**引用**: {cite_links}")
        lines.append("")

    # Reading notes template
    lines.append("## 阅读笔记")
    lines.append("")
    lines.append("### 核心贡献")
    lines.append("- ")
    lines.append("")
    lines.append("### 方法概要")
    lines.append("- ")
    lines.append("")
    lines.append("### 实验亮点")
    lines.append("- ")
    lines.append("")
    lines.append("### 与我的研究关联")
    lines.append("- ")
    lines.append("")
    lines.append("### 待读 / 跟进")
    lines.append("- [ ] ")
    lines.append("")

    return "\n".join(lines)


def write_paper_notes(
    papers: list[dict],
    output_dir: str | Path,
    db=None,
    skip_existing: bool = True,
) -> dict:
    """
    Write Obsidian notes for a list of papers.

    Args:
        papers: list of paper dicts
        output_dir: directory to write .md files
        db: PaperDB instance for relation lookups
        skip_existing: skip papers that already have notes

    Returns:
        stats dict
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    skipped = 0
    errors = 0

    for paper in papers:
        title = paper.get("title", paper["id"])
        filename = _sanitize_filename(title) + ".md"
        filepath = output_dir / filename

        if skip_existing and filepath.exists():
            skipped += 1
            continue

        try:
            note = paper_to_obsidian_note(paper, db=db)
            filepath.write_text(note, encoding="utf-8")
            written += 1
        except Exception as e:
            logger.error(f"Error writing note for {paper['id']}: {e}")
            errors += 1

    stats = {"written": written, "skipped": skipped, "errors": errors, "dir": str(output_dir)}
    logger.info(f"Obsidian notes: {stats}")
    return stats


# ─────────────────────── CLI Test ───────────────────────

if __name__ == "__main__":
    import tempfile
    logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")

    test_paper = {
        "id": "2603.03276v1",
        "title": "Beyond Language Modeling: An Exploration of Multimodal Pretraining",
        "abstract": "We provide empirical clarity through controlled experiments.",
        "cn_abstract": "本文通过受控实验系统梳理多模态预训练核心影响因素。RAE提供最优统一视觉表征。",
        "cn_oneliner": "LeCun团队实验揭示多模态预训练关键规律，RAE是最优视觉表征。",
        "authors": '["Shengbang Tong", "Saining Xie", "Yann LeCun"]',
        "date": "2026-03-03",
        "domain": "Unified Understanding & Generation",
        "best_score": 0.72,
        "paper_type": "方法文",
        "labels": '["⭐ VIP:Shengbang Tong", "📝 方法文"]',
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        note = paper_to_obsidian_note(test_paper)
        print(note[:1500])
        print("\n[... truncated ...]\n")

        stats = write_paper_notes([test_paper], tmpdir)
        print(f"\nWrite stats: {stats}")

        files = list(Path(tmpdir).glob("*.md"))
        print(f"Files written: {[f.name for f in files]}")

    print("\n✅ Obsidian writer test complete!")
