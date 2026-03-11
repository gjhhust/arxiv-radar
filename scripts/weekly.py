"""
weekly.py — Weekly report generator for arxiv-radar.

Aggregates 7 days of papers, deduplicates, identifies weekly highlights,
and outputs an Obsidian-compatible weekly report markdown file.
"""

from __future__ import annotations
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from aggregator import aggregate_date_range
from recommender import score_paper
from config_parser import parse_config

logger = logging.getLogger(__name__)

# Base paths (derived from script location, not hardcoded)
SCRIPTS_DIR = Path(__file__).parent
SKILL_DIR = SCRIPTS_DIR.parent
CACHE_DIR = SKILL_DIR / "data" / "cache"
DB_PATH = SKILL_DIR / "data" / "paper_network.db"


# ─────────────────────────── Helpers ───────────────────────────

def _week_id(start: date) -> str:
    """Return ISO week string like 2026-W10."""
    iso = start.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _domain_emoji(name: str) -> str:
    n = name.lower()
    if "token" in n or "1d" in n:
        return "🔢"
    if "unified" in n or "multimodal" in n or "统一" in n:
        return "🔗"
    if "world" in n or "世界" in n:
        return "🌍"
    if "diffusion" in n or "generat" in n:
        return "🎨"
    return "🔬"


def _format_authors(authors: list[str], n: int = 3) -> str:
    if len(authors) <= n:
        return ", ".join(authors)
    return ", ".join(authors[:n]) + f" et al."


def _score_label(score: float) -> str:
    if score >= 0.70:
        return "强相关"
    if score >= 0.50:
        return "中相关"
    return "弱相关"


def _labels_inline(labels: list[str]) -> str:
    return " · ".join(labels) if labels else ""


def _abstract_snippet(abstract: str, n: int = 250) -> str:
    if not abstract:
        return ""
    if len(abstract) <= n:
        return abstract
    cut = abstract[:n]
    p = cut.rfind(". ")
    return (cut[:p + 1] if p > n * 0.6 else cut) + "..."


# ─────────────────────────── Obsidian Frontmatter ───────────────────────────

def _frontmatter(start: date, end: date, total: int, domains: list[dict]) -> str:
    domain_tags = " ".join(
        f"arxiv/{d['name'].replace(' ', '-').replace('&', 'and')}"
        for d in domains
    )
    return f"""---
tags: [科研追踪, 周报, arxiv, {domain_tags}]
created: {date.today().isoformat()}
week: {_week_id(start)}
period: {start.isoformat()} ~ {end.isoformat()}
total_papers: {total}
---
"""


# ─────────────────────────── Report Sections ───────────────────────────

def _render_header(start: date, end: date, stats: dict) -> str:
    week = _week_id(start)
    total_relevant = stats.get("total_relevant", 0)
    total_crawled = stats.get("total_crawled", 0)
    strong = sum(
        1 for d_papers in stats.get("_domain_papers_ref", {}).values()
        for p in d_papers
        if p.get("best_score", 0) >= 0.70
    )

    lines = [
        f"# 科研追踪周报 — {start.isoformat()} → {end.isoformat()}",
        "",
        f"> 生成时间: {date.today().isoformat()}  ",
        f"> 检索区间: {start.isoformat()} → {end.isoformat()} ({stats.get('days', 7)} 天)  ",
        f"> 爬取论文: **{total_crawled}** 篇 | 去重后相关: **{total_relevant}** 篇"
        f"（强相关 {strong} / 中相关 {total_relevant - strong}）",
        "",
    ]
    return "\n".join(lines)


def _render_overview_table(stats: dict, domains: list[dict]) -> str:
    lines = [
        "## 快速总览",
        "",
        "| 领域 | 新增 | 强相关 | 必读 |",
        "|---|---:|---:|---:|",
    ]
    per_domain = stats.get("per_domain", {})
    domain_papers_ref = stats.get("_domain_papers_ref", {})
    recommend_ref = stats.get("_recommend_ref", {})

    for i, domain in enumerate(domains):
        name = domain["name"]
        emoji = _domain_emoji(name)
        count = per_domain.get(name, 0)
        papers = domain_papers_ref.get(f"domain_{i}", [])
        strong = sum(1 for p in papers if p.get("best_score", 0) >= 0.70)
        n_recs = len(recommend_ref.get(f"domain_{i}", {}).get("recommendations", []))
        lines.append(f"| {emoji} {name} | {count} | {strong} | {n_recs} |")

    return "\n".join(lines)


def _render_domain_section(
    domain: dict,
    domain_key: str,
    domain_papers: list[dict],
    recommendations: dict,
) -> str:
    name = domain["name"]
    emoji = _domain_emoji(name)
    recs = recommendations.get(domain_key, {}).get("recommendations", [])

    lines = [
        f"\n---\n",
        f"## {emoji} {name}",
        "",
    ]

    # Must-reads
    if recs:
        lines.append("### ⭐ 必读清单")
        lines.append("")
        for r in recs:
            p = r["paper"]
            score = int(p.get("best_score", 0) * 100)
            url = p.get("arxiv_url", "")
            title = p["title"]
            authors = _format_authors(p.get("authors", []))
            lines.append(
                f"- **{title}**（score={score}）  \n"
                f"  👤 {authors} | {url}"
            )
        lines.append("")

    # Weekly analysis box
    lines.append("### 💡 本周技术要点")
    lines.append("")

    # Build from top papers' abstracts
    top_papers = domain_papers[:5]
    if top_papers:
        titles_str = "、".join(f"*{p['title'][:40]}*" for p in top_papers[:3])
        lines.append(
            f"1. **本周最值得读**：{titles_str} 等 {len(domain_papers)} 篇相关论文进入视野。"
        )
        # VIP papers
        vip_papers = [p for p in domain_papers if any("VIP" in l for l in p.get("labels", []))]
        if vip_papers:
            vip_str = "、".join(
                f"{p['title'][:35]}（{next(l for l in p['labels'] if 'VIP' in l)}）"
                for p in vip_papers[:2]
            )
            lines.append(f"2. **大牛论文**：{vip_str}")
        # Open source
        os_papers = [p for p in domain_papers if any("open-source" in l for l in p.get("labels", []))]
        if os_papers:
            lines.append(f"3. **开源论文**：本周 {len(os_papers)} 篇论文开放代码，重点关注：{os_papers[0]['title'][:50]}")

    lines.append("")

    # Full paper list
    lines.append("### 📚 论文列表")
    lines.append("")

    for rank, paper in enumerate(domain_papers):
        score = paper.get("similarity_scores", {}).get(name, 0) or paper.get("best_score", 0)
        score_int = int(score * 100)
        score_tag = _score_label(score)
        labels = paper.get("labels", [])
        is_mustreads = any(
            r["paper"]["id"] == paper["id"]
            for r in recs
        )

        prefix = "**[必读]** " if is_mustreads else ""
        label_str = _labels_inline(labels)
        authors = _format_authors(paper.get("authors", []))
        url = paper.get("arxiv_url", "")
        snippet = _abstract_snippet(paper.get("abstract", ""), 200)

        lines.append(f"- {prefix}**{paper['title']}**")
        lines.append(f"  - 👤 {authors} | 📅 {paper.get('date', '')} | 📈 相关性 **{score_int}**（{score_tag}）")
        if label_str:
            lines.append(f"  - 🏷️ {label_str}")
        lines.append(f"  - 🔗 {url}")

        # Chinese analysis (if available)
        cn_oneliner = paper.get("cn_oneliner", "")
        cn_abstract = paper.get("cn_abstract", "")
        graph_context = paper.get("graph_context", "")
        if cn_oneliner:
            lines.append(f"  - 💡 **一句话**: {cn_oneliner}")
        if cn_abstract:
            lines.append(f"  - > {cn_abstract}")
        elif snippet:
            lines.append(f"  - > {snippet}")
        # Knowledge graph context (only for must-reads to avoid bloat)
        if graph_context and is_mustreads:
            for ctx_line in graph_context.strip().split("\n"):
                if ctx_line.strip():
                    lines.append(f"  - {ctx_line}")
        lines.append("")

    return "\n".join(lines)


def _render_next_week_tips(domain_papers_all: dict, domains: list[dict]) -> str:
    """Generate next-week tracking suggestions."""
    lines = [
        "\n---\n",
        "## 🔭 下周追踪建议",
        "",
    ]

    for i, domain in enumerate(domains):
        name = domain["name"]
        emoji = _domain_emoji(name)
        papers = domain_papers_all.get(f"domain_{i}", [])
        if not papers:
            continue

        # Extract keyword hints from top paper titles
        top_titles = [p["title"] for p in papers[:3]]
        lines.append(f"**{emoji} {name}：**")

        # Get VIP authors seen this week
        vip_seen = set()
        for p in papers:
            for l in p.get("labels", []):
                if "VIP:" in l:
                    vip_seen.add(l.replace("⭐ VIP:", ""))
        if vip_seen:
            lines.append(f"- 大牛持续追踪：{', '.join(list(vip_seen)[:3])}")

        # Keyword suggestions from config
        keywords = domain.get("keywords", [])
        if keywords:
            lines.append(f"- 核心关键词：{', '.join(keywords[:5])}")

        lines.append("")

    return "\n".join(lines)


# ─────────────────────────── Main Generator ───────────────────────────

def generate_weekly_report(
    start_date: date,
    end_date: date,
    config: dict,
    domains: list[dict],
    output_dir: str | Path | None = None,
    force_refresh: bool = False,
    skip_analysis: bool = False,
) -> str:
    """
    Generate a weekly report for the given date range.

    Args:
        start_date: Start of week (Monday)
        end_date: End of week (Sunday)
        config: Parsed config
        domains: Domain definitions
        output_dir: Where to save (defaults to ~/arxiv-daily/周报/)
        force_refresh: Re-fetch even if cached
        skip_analysis: Skip LLM-based Chinese analysis

    Returns:
        Report markdown string. Also saves to output_dir.
    """
    from recommender import recommend
    from analyzer import analyze_papers, enrich_papers

    logger.info(f"Generating weekly report: {start_date} → {end_date}")

    # 1. Aggregate
    agg = aggregate_date_range(start_date, end_date, config, domains, force_refresh)
    domain_papers = agg["domain_papers"]
    stats = agg["stats"]

    # Inject refs for rendering helpers
    stats["_domain_papers_ref"] = domain_papers
    stats["_recommend_ref"] = {}

    # 2. Recommend top papers from weekly pool
    recommendations = recommend(domain_papers, config, domains)
    stats["_recommend_ref"] = recommendations

    # 3. Chinese analysis for all relevant papers
    if not skip_analysis:
        import json as _json
        all_relevant = []
        for key in sorted(domain_papers.keys()):
            if key.startswith("domain_"):
                all_relevant.extend(domain_papers[key])

        # Try loading pre-computed analysis first
        precomputed = CACHE_DIR / "analysis_merged.json"
        pre_analyses = {}
        if precomputed.exists():
            try:
                raw = _json.loads(precomputed.read_text(encoding="utf-8"))
                pre_analyses = raw if isinstance(raw, dict) else {item["id"]: item for item in raw}
                logger.info(f"Loaded {len(pre_analyses)} pre-computed analyses")
            except Exception as e:
                logger.warning(f"Failed to load precomputed analysis: {e}")

        if all_relevant:
            # Enrich from pre-computed
            if pre_analyses:
                enriched = 0
                for key in domain_papers:
                    if key.startswith("domain_"):
                        for paper in domain_papers[key]:
                            analysis = pre_analyses.get(paper["id"], {})
                            if analysis.get("cn_abstract"):
                                paper["cn_abstract"] = analysis["cn_abstract"]
                                paper["cn_oneliner"] = analysis.get("cn_oneliner", "")
                                # Update paper_type from LLM if available (overrides keyword-based)
                                if analysis.get("paper_type"):
                                    new_type = analysis["paper_type"]
                                    paper["paper_type"] = new_type
                                    # Sync labels
                                    labels = paper.get("labels", [])
                                    labels = [l for l in labels if l not in ("📝 方法文", "📊 Benchmark", "🔬 Survey")]
                                    type_map = {"方法文": "📝 方法文", "Benchmark": "📊 Benchmark", "Survey": "🔬 Survey"}
                                    labels.append(type_map.get(new_type, f"📝 {new_type}"))
                                    paper["labels"] = labels
                                enriched += 1
                logger.info(f"Enriched {enriched}/{len(all_relevant)} papers from cache")

            # For remaining papers without analysis, try LLM
            unenriched = [p for p in all_relevant if not p.get("cn_abstract")]
            if unenriched:
                logger.info(f"Running Chinese analysis on {len(unenriched)} remaining papers...")
                analyses = analyze_papers(unenriched, use_llm=True)
                for key in domain_papers:
                    if key.startswith("domain_"):
                        enrich_papers(domain_papers[key], analyses)

    # 4. Context injection from knowledge graph (if DB available)
    db = None
    if DB_PATH.exists():
        try:
            from paper_db import PaperDB
            from context_injector import enrich_weekly_analysis
            db = PaperDB(DB_PATH)
            ctx_stats = enrich_weekly_analysis(domain_papers, db)
            logger.info(f"Graph context injection: {ctx_stats}")
        except Exception as e:
            logger.warning(f"Context injection skipped: {e}")

    # 5. Render
    sections = [
        _frontmatter(start_date, end_date, stats["total_relevant"], domains),
        _render_header(start_date, end_date, stats),
        _render_overview_table(stats, domains),
    ]

    for i, domain in enumerate(domains):
        key = f"domain_{i}"
        papers = domain_papers.get(key, [])
        sections.append(
            _render_domain_section(domain, key, papers, recommendations)
        )

    sections.append(_render_next_week_tips(domain_papers, domains))

    # Trend radar + Idea seeds
    try:
        from trend import compute_trends, format_trend_section, generate_idea_seeds
        all_papers_flat = [
            p for key, papers in domain_papers.items()
            if key.startswith("domain_")
            for p in papers
        ]
        trends = compute_trends(all_papers_flat)
        sections.append(format_trend_section(trends, period_label=f"{start_date} ~ {end_date}"))
        sections.append(generate_idea_seeds(domain_papers, domains, db=db))
    except Exception as e:
        logger.warning(f"Trend/Idea section skipped: {e}")

    # Exploration branches (method variant clustering)
    if db:
        try:
            from reference_ranker import get_exploration_branches, format_exploration_branches
            branches = get_exploration_branches(db, min_papers=2)
            if branches:
                sections.append(format_exploration_branches(branches))
        except Exception as e:
            logger.warning(f"Exploration branches skipped: {e}")


    # Footer
    sections.append(
        f"\n---\n\n*Generated by arxiv-radar · {date.today().isoformat()}*\n"
        f"*Embedding model: {config.get('embedding_model', 'all-MiniLM-L6-v2')}*\n"
    )

    report = "\n".join(sections)

    # 4. Save
    if output_dir is None:
        output_dir = Path(config.get("report_path", "~/arxiv-daily/")).expanduser() / "周报"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    week = _week_id(start_date)
    filename = f"{start_date.isoformat()}-周报.md"
    filepath = output_dir / filename
    filepath.write_text(report, encoding="utf-8")
    logger.info(f"Weekly report saved: {filepath}")

    return report


# ─────────────────────────── CLI ───────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")

    parser = argparse.ArgumentParser(description="Generate weekly arxiv report")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--config", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--skip-analysis", action="store_true",
                        help="Skip LLM Chinese analysis")
    args = parser.parse_args()

    from datetime import datetime
    skill_dir = Path(__file__).parent.parent
    cfg_path = args.config or skill_dir / "config.template.md"
    config = parse_config(cfg_path)
    domains = config["domains"]

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()

    report = generate_weekly_report(
        start, end, config, domains,
        output_dir=args.output_dir,
        force_refresh=args.force_refresh,
        skip_analysis=args.skip_analysis,
    )
    print(report[:2000])
    print(f"\n... (total {len(report)} chars)")
