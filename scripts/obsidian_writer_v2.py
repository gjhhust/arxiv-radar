"""
obsidian_writer_v2.py — Enhanced Obsidian note generator for arxiv-radar v3.0.

Builds on obsidian_writer.py with v3 schema additions:
  - YAML frontmatter: contribution_type, editorial_note, why_read
  - "方法谱系" section: [[base_method]] wikilinks from method_variants table
  - "引用关系" section: [[Title]] — `stance` — note  (from key_refs JSON field)
  - New: write_method_hub() — writes _hubs/<base_method>.md method-hub notes
  - New: write_all_v2()    — batch-writes all paper notes + all method hubs

Usage:
  python3 scripts/obsidian_writer_v2.py --output ~/mydata/notes/arxiv-radar/ --batch 50
"""

from __future__ import annotations
import argparse
import json
import logging
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))


# ─────────────────── Shared helpers (reused from v1) ───────────────────

def _sanitize_filename(title: str, max_len: int = 80) -> str:
    """Convert paper title to a safe filename."""
    safe = re.sub(r"[^\w\s\-]", "", title)
    safe = re.sub(r"\s+", " ", safe).strip()
    return safe[:max_len]


def _format_authors(authors_raw, n: int = 3) -> str:
    """Format authors from JSON string or list."""
    try:
        authors = json.loads(authors_raw) if isinstance(authors_raw, str) else authors_raw
        if not authors:
            return "Unknown"
        if len(authors) <= n:
            return ", ".join(authors)
        return ", ".join(authors[:n]) + " et al."
    except Exception:
        return str(authors_raw)[:100]


def _get_baselines(paper_id: str, db) -> list[str]:
    """Get canonical baseline names for a paper from DB."""
    if db is None:
        return []
    try:
        return db.get_baselines_for_paper(paper_id)
    except Exception:
        return []


def _get_related_papers(paper_id: str, db, max_per_type: int = 5) -> dict:
    """Get related papers grouped by edge type."""
    try:
        neighbors = db.get_neighbors(paper_id, direction="out")
    except Exception:
        return {}
    by_type: dict[str, list] = {}
    for n in neighbors:
        etype = n["edge_type"]
        if etype not in by_type:
            by_type[etype] = []
        if len(by_type[etype]) < max_per_type:
            nbr = db.get_paper(n["neighbor_id"])
            if nbr:
                by_type[etype].append({
                    "id":     n["neighbor_id"],
                    "title":  nbr["title"],
                    "weight": n["weight"],
                })
    return by_type


def _get_method_variants_for_paper(paper_id: str, db) -> list[dict]:
    """
    Return method_variants rows for a paper from the method_variants DB table.

    Returns list of dicts with keys: base_method, variant_tag, description.
    Falls back to parsing paper.method_variants JSON field if table unavailable.
    """
    if db is None:
        return []
    try:
        db.ensure_method_variants_table()
        conn = db._connect()
        try:
            rows = conn.execute(
                "SELECT base_method, variant_tag, description FROM method_variants WHERE paper_id = ?",
                (paper_id,),
            ).fetchall()
            return [{"base_method": r[0], "variant_tag": r[1], "description": r[2]} for r in rows]
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"method_variants table query failed ({e}), using JSON field")
        return []


def _parse_key_refs(paper: dict) -> list[dict]:
    """
    Parse key_refs from the paper dict.

    key_refs may be stored as:
      - paper["key_refs"] already a list
      - paper["key_refs"] a JSON string
    Each entry: {"title": "...", "stance": "...", "note": "..."}
    """
    raw = paper.get("key_refs")
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    return []


# ─────────────────── V2 paper note ───────────────────

def paper_to_obsidian_note_v2(paper: dict, db=None) -> str:
    """
    Generate enhanced Obsidian note markdown for a single paper (v3 schema).

    Adds over v1:
      - YAML: contribution_type, editorial_note, why_read
      - Section "方法谱系": base_method [[wikilinks]]
      - Section "引用关系": key_refs with stance annotation

    Args:
        paper: paper dict (may contain v3 fields or not)
        db:    PaperDB instance for method_variants and relation lookups

    Returns:
        Markdown string.
    """
    pid              = paper["id"]
    title            = paper.get("title", "Unknown")
    abstract         = paper.get("abstract", "")
    cn_abstract      = paper.get("cn_abstract", "")
    cn_oneliner      = paper.get("cn_oneliner", "")
    domain           = paper.get("domain", "")
    score            = paper.get("best_score", 0)
    paper_type       = paper.get("paper_type", "方法文")
    date_str         = (paper.get("date", "") or "")[:10]
    authors_raw      = paper.get("authors", "[]")
    authors          = _format_authors(authors_raw)

    # v3 fields
    contribution_type = paper.get("contribution_type", "")
    editorial_note    = paper.get("editorial_note", "")
    why_read          = paper.get("why_read", "")

    # Tags
    type_tag   = re.sub(r"[^\w\-/]", "", paper_type.replace(" ", "-"))
    domain_tag = re.sub(r"[^\w\-/]", "", domain.replace(" ", "-").replace("&", "and"))
    labels_raw = paper.get("labels", "[]")
    try:
        labels = json.loads(labels_raw) if isinstance(labels_raw, str) else labels_raw
    except Exception:
        labels = []
    label_tags = [re.sub(r"[^\w\-/]", "", l.replace(" ", "-")) for l in labels if l]

    # DB lookups
    baselines       = _get_baselines(pid, db)
    related         = _get_related_papers(pid, db)
    method_variants = _get_method_variants_for_paper(pid, db)

    # If no DB variants, fall back to paper JSON field
    if not method_variants:
        raw_mv = paper.get("method_variants")
        if raw_mv:
            try:
                method_variants = (
                    json.loads(raw_mv) if isinstance(raw_mv, str) else raw_mv
                )
            except Exception:
                method_variants = []

    key_refs = _parse_key_refs(paper)

    # Unique base methods for wikilinks
    base_methods = list(dict.fromkeys(
        v.get("base_method", "") for v in method_variants if v.get("base_method")
    ))

    lines: list[str] = []

    # ── YAML frontmatter ──────────────────────────────────────────────────
    lines.append("---")
    lines.append(f"id: {pid}")
    lines.append(f'title: "{title.replace(chr(34), chr(39))}"')
    lines.append(f"date: {date_str}")
    lines.append(f"domain: {domain_tag}")
    lines.append(f"score: {int(score * 100)}")
    lines.append(f"paper_type: {paper_type}")
    if contribution_type:
        lines.append(f"contribution_type: {contribution_type}")
    if editorial_note:
        safe_note = editorial_note.replace('"', "'")
        lines.append(f'editorial_note: "{safe_note}"')
    if why_read:
        safe_why = why_read.replace('"', "'")
        lines.append(f'why_read: "{safe_why}"')
    if baselines:
        lines.append(f"baselines: [{', '.join(baselines[:6])}]")
    if base_methods:
        lines.append(f"method_bases: [{', '.join(base_methods[:6])}]")
    lines.append("status: unread")
    lines.append("tags:")
    lines.append("  - 科研追踪")
    if domain_tag:
        lines.append(f"  - {domain_tag}")
    if type_tag:
        lines.append(f"  - {type_tag}")
    if contribution_type:
        lines.append(f"  - {contribution_type}")
    for lt in label_tags[:3]:
        if lt:
            lines.append(f"  - {lt}")
    lines.append(f"arxiv_url: https://arxiv.org/abs/{pid}")
    lines.append(f"created: {date.today().isoformat()}")
    lines.append("---")
    lines.append("")

    # ── Title + metadata ──────────────────────────────────────────────────
    lines.append(f"# {title}")
    lines.append("")
    lines.append(
        f"👤 **{authors}** | 📅 {date_str} | 📈 相关性 **{int(score*100)}** | 🏷️ {paper_type}"
    )
    lines.append(f"🔗 [arXiv:{pid}](https://arxiv.org/abs/{pid})")
    lines.append("")

    # ── One-liner + contribution badge ───────────────────────────────────
    if cn_oneliner:
        lines.append(f"> 💡 **{cn_oneliner}**")
        lines.append("")
    if contribution_type:
        lines.append(f"**贡献类型:** `{contribution_type}`", )
        if why_read:
            lines[-1] += f"  |  **推荐:** {why_read}"
        lines.append("")
    if editorial_note:
        lines.append(f"> 🦊 **Mox says:** {editorial_note}")
        lines.append("")

    # ── Chinese / English abstract ────────────────────────────────────────
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

    # ── 方法谱系 (method lineage, v3) ────────────────────────────────────
    if method_variants:
        lines.append("## 方法谱系")
        lines.append("")
        # Base method wikilinks
        if base_methods:
            wikilinks = " · ".join(f"[[{bm}]]" for bm in base_methods)
            lines.append(f"**基础方法:** {wikilinks}")
            lines.append("")
        # Individual variant tags
        for v in method_variants:
            tag  = v.get("variant_tag", "")
            desc = v.get("description", "")
            lines.append(f"- `{tag}` — {desc}")
        lines.append("")

    # ── 引用关系 (key refs with stance, v3) ──────────────────────────────
    if key_refs:
        lines.append("## 引用关系")
        lines.append("")
        for kr in key_refs:
            ref_title = kr.get("title", "")
            stance    = kr.get("stance", "")
            note      = kr.get("note", "")
            safe_title = _sanitize_filename(ref_title)
            if stance:
                lines.append(f"- [[{safe_title}]] — `{stance}` — {note}")
            else:
                lines.append(f"- [[{safe_title}]] — {note}")
        lines.append("")

    # ── Method lineage (v1 style, baseline / EXTENDS / COMPARES_WITH) ────
    if baselines or related.get("EXTENDS") or related.get("COMPARES_WITH"):
        lines.append("## 方法线索")
        lines.append("")
        if related.get("EXTENDS"):
            ext_links = " · ".join(
                f"[[{_sanitize_filename(r['title'])}]]" for r in related["EXTENDS"]
            )
            lines.append(f"**继承自:** {ext_links}")
        if baselines:
            bl_links = " · ".join(f"[[{b}]]" for b in baselines[:6])
            lines.append(f"**对比 Baseline:** {bl_links}")
        if related.get("COMPARES_WITH"):
            peer_links = " · ".join(
                f"[[{_sanitize_filename(r['title'])}]]" for r in related["COMPARES_WITH"][:3]
            )
            lines.append(f"**同线路论文:** {peer_links}")
        if related.get("CITES"):
            cite_links = " · ".join(
                f"[[{_sanitize_filename(r['title'])}]]" for r in related["CITES"][:3]
            )
            lines.append(f"**引用:** {cite_links}")
        lines.append("")

    # ── Reading notes template ────────────────────────────────────────────
    lines += [
        "## 阅读笔记",
        "",
        "### 核心贡献",
        "- ",
        "",
        "### 方法概要",
        "- ",
        "",
        "### 实验亮点",
        "- ",
        "",
        "### 与我的研究关联",
        "- ",
        "",
        "### 待读 / 跟进",
        "- [ ] ",
        "",
    ]

    return "\n".join(lines)


# ─────────────────── Method hub ───────────────────

def write_method_hub(base_method: str, papers: list[dict], output_dir: Path) -> Path:
    """
    Write a method-hub note for a given base_method.

    Creates: <output_dir>/_hubs/<base_method>.md

    Each paper entry in `papers` should have keys:
      paper_id, variant_tag, description, title, date

    Args:
        base_method:  canonical base method name (e.g. "titok")
        papers:       list of variant paper dicts (from DB get_exploration_branches)
        output_dir:   root Obsidian output directory

    Returns:
        Path to the written hub file.
    """
    hubs_dir = output_dir / "_hubs"
    hubs_dir.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r"[^\w\-]", "", base_method.lower())
    hub_path  = hubs_dir / f"{safe_name}.md"

    lines: list[str] = []

    # YAML frontmatter
    lines += [
        "---",
        "type: method-hub",
        f"method: {base_method}",
        f"paper_count: {len(papers)}",
        "---",
        "",
        f"# {base_method} — 方法谱系",
        "",
        "## 变体论文",
        "",
    ]

    for p in papers:
        pid        = p.get("paper_id", "")
        variant    = p.get("variant_tag", "")
        p_title    = p.get("title", pid)
        p_date     = (p.get("date", "") or "")[:7]        # YYYY-MM
        desc       = p.get("description", "")

        safe_title = _sanitize_filename(p_title)
        variant_suffix = variant.split(":", 1)[-1] if ":" in variant else variant

        entry = f"- [[{safe_title}]] `{variant}` ({p_date})"
        if desc:
            entry += f"  \n  > {desc}"
        lines.append(entry)

    lines.append("")

    hub_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Method hub written: {hub_path}")
    return hub_path


# ─────────────────── Batch writer ───────────────────

def write_all_v2(db, output_dir: Path, batch: int = 50) -> dict:
    """
    Batch-write all paper notes (with v3 fields) and all method hub notes.

    Steps:
      1. Load all papers from DB in batches of `batch`
      2. Write each paper's Obsidian note (v2 format)
      3. Aggregate method_variants → write one hub per base_method

    Args:
        db:         PaperDB instance
        output_dir: root directory for Obsidian vault output
        batch:      papers per DB query page

    Returns:
        Stats dict: {papers_written, papers_skipped, papers_error,
                     hubs_written, hub_methods}
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    papers_written = 0
    papers_skipped = 0
    papers_error   = 0

    # Write paper notes in batches (paginate with LIMIT/OFFSET)
    offset = 0
    conn = db._connect()
    try:
        total_papers = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    finally:
        conn.close()

    logger.info(f"Total papers: {total_papers} | batch={batch}")

    while offset < total_papers:
        conn = db._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM papers ORDER BY date DESC LIMIT ? OFFSET ?",
                (batch, offset),
            ).fetchall()
        finally:
            conn.close()

        for row in rows:
            paper = dict(row)
            pid   = paper.get("id", "")
            title = paper.get("title", pid)
            filename = _sanitize_filename(title) + ".md"
            filepath = output_dir / filename

            try:
                note = paper_to_obsidian_note_v2(paper, db=db)
                filepath.write_text(note, encoding="utf-8")
                papers_written += 1
            except Exception as e:
                logger.error(f"Error writing note for {pid}: {e}")
                papers_error += 1

        offset += batch
        logger.info(f"Progress: {min(offset, total_papers)}/{total_papers}")

    # ── Write method hubs ─────────────────────────────────────────────────
    hubs_written = 0
    hub_methods: list[str] = []

    try:
        branches = db.get_exploration_branches(min_papers=1)
        for branch in branches:
            base_method = branch["base_method"]
            papers_list = branch["papers"]
            try:
                write_method_hub(base_method, papers_list, output_dir)
                hubs_written += 1
                hub_methods.append(base_method)
            except Exception as e:
                logger.error(f"Error writing hub for {base_method}: {e}")
    except Exception as e:
        logger.warning(f"Could not generate method hubs: {e}")

    stats = {
        "papers_written": papers_written,
        "papers_skipped": papers_skipped,
        "papers_error":   papers_error,
        "hubs_written":   hubs_written,
        "hub_methods":    hub_methods,
        "output_dir":     str(output_dir),
    }
    logger.info(f"write_all_v2 done: {stats}")
    return stats


# ─────────────────── CLI ───────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="arxiv-radar v3 Obsidian writer (enhanced with method hubs)",
    )
    parser.add_argument(
        "--output",
        default=str(Path.home() / "mydata" / "notes" / "arxiv-radar"),
        help="Output directory for Obsidian vault",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=50,
        help="Papers per DB query batch (default: 50)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")

    from paper_db import PaperDB
    db = PaperDB()

    output_dir = Path(args.output).expanduser()
    print(f"Writing to: {output_dir}")

    stats = write_all_v2(db, output_dir, batch=args.batch)
    print(f"\nDone: {stats}")


if __name__ == "__main__":
    main()
