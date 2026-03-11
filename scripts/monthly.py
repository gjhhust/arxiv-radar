"""
monthly.py — Monthly report generator for arxiv-radar.

Aggregates the full month, identifies monthly highlights and trends,
outputs an Obsidian-compatible monthly report.
"""

from __future__ import annotations
import calendar
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from aggregator import aggregate_date_range
from recommender import recommend, score_paper
from config_parser import parse_config
from weekly import (
    _frontmatter, _domain_emoji, _format_authors,
    _score_label, _labels_inline, _abstract_snippet,
)

logger = logging.getLogger(__name__)


def _month_date_range(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _monthly_frontmatter(year: int, month: int, total: int, domains: list[dict]) -> str:
    domain_tags = " ".join(
        f"arxiv/{d['name'].replace(' ', '-').replace('&', 'and')}"
        for d in domains
    )
    return f"""---
tags: [科研追踪, 月报, arxiv, {domain_tags}]
created: {date.today().isoformat()}
year: {year}
month: {month:02d}
period: {year}-{month:02d}-01 ~ {year}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}
total_papers: {total}
---
"""


def _render_monthly_header(year: int, month: int, stats: dict) -> str:
    start, end = _month_date_range(year, month)
    total_relevant = stats.get("total_relevant", 0)
    total_crawled = stats.get("total_crawled", 0)
    domain_papers_ref = stats.get("_domain_papers_ref", {})
    strong = sum(
        1 for papers in domain_papers_ref.values()
        for p in papers if p.get("best_score", 0) >= 0.70
    )

    return (
        f"# 科研追踪月报 — {year}-{month:02d}-01 → {end.isoformat()}\n\n"
        f"> 生成时间: {date.today().isoformat()}  \n"
        f"> 检索区间: {start.isoformat()} → {end.isoformat()}  \n"
        f"> 爬取论文: **{total_crawled}** 篇 | 去重后相关: **{total_relevant}** 篇"
        f"（强相关 {strong} / 中相关 {total_relevant - strong}）\n"
    )


def _render_monthly_overview(stats: dict, domains: list[dict]) -> str:
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


def _render_monthly_domain(
    domain: dict,
    domain_key: str,
    domain_papers: list[dict],
    recommendations: dict,
    top_k_list: int = 20,
) -> str:
    name = domain["name"]
    emoji = _domain_emoji(name)
    recs = recommendations.get(domain_key, {}).get("recommendations", [])
    rec_ids = {r["paper"]["id"] for r in recs}

    lines = [
        f"\n---\n",
        f"## {emoji} {name}",
        "",
        "### ⭐ 必读清单",
        "",
    ]

    for r in recs:
        p = r["paper"]
        score_int = int(p.get("best_score", 0) * 100)
        url = p.get("arxiv_url", "")
        lines.append(
            f"- **{p['title']}**（score={score_int}）  \n"
            f"  👤 {_format_authors(p.get('authors', []))} | 📅 {p.get('date', '')} | {url}"
        )
    lines.append("")

    # Monthly analysis
    lines.append("### 💡 本月技术要点")
    lines.append("")
    if domain_papers:
        lines.append(f"本月共追踪到 **{len(domain_papers)}** 篇相关论文。")
        vip_papers = [p for p in domain_papers if any("VIP" in l for l in p.get("labels", []))]
        os_papers = [p for p in domain_papers if any("open-source" in l for l in p.get("labels", []))]
        if vip_papers:
            lines.append(
                f"- 大牛论文 **{len(vip_papers)}** 篇："
                + "、".join(f"{p['title'][:35]}" for p in vip_papers[:3])
            )
        if os_papers:
            lines.append(f"- 开源论文 **{len(os_papers)}** 篇，代码可复现。")

        # Top by score
        top3 = domain_papers[:3]
        lines.append(f"- 月度最高分：{top3[0]['title'][:60]}（score={int(top3[0].get('best_score',0)*100)}）")
    lines.append("")

    # Paper list (top N)
    lines.append(f"### 📚 论文列表（Top {min(top_k_list, len(domain_papers))}）")
    lines.append("")

    for paper in domain_papers[:top_k_list]:
        score = paper.get("best_score", 0)
        score_int = int(score * 100)
        score_tag = _score_label(score)
        labels = paper.get("labels", [])
        is_mustread = paper["id"] in rec_ids
        prefix = "**[必读]** " if is_mustread else ""
        url = paper.get("arxiv_url", "")
        snippet = _abstract_snippet(paper.get("abstract", ""), 180)

        lines.append(f"- {prefix}**{paper['title']}**")
        lines.append(
            f"  - 👤 {_format_authors(paper.get('authors', []))} "
            f"| 📅 {paper.get('date', '')} "
            f"| 📈 相关性 **{score_int}**（{score_tag}）"
        )
        if labels:
            lines.append(f"  - 🏷️ {_labels_inline(labels)}")
        lines.append(f"  - 🔗 {url}")
        lines.append(f"  - > {snippet}")
        lines.append("")

    return "\n".join(lines)


def generate_monthly_report(
    year: int,
    month: int,
    config: dict,
    domains: list[dict],
    output_dir: str | Path | None = None,
    force_refresh: bool = False,
) -> str:
    start, end = _month_date_range(year, month)
    logger.info(f"Generating monthly report: {start} → {end}")

    agg = aggregate_date_range(start, end, config, domains, force_refresh)
    domain_papers = agg["domain_papers"]
    stats = agg["stats"]
    stats["_domain_papers_ref"] = domain_papers

    recommendations = recommend(domain_papers, config, domains)
    stats["_recommend_ref"] = recommendations

    sections = [
        _monthly_frontmatter(year, month, stats["total_relevant"], domains),
        _render_monthly_header(year, month, stats),
        _render_monthly_overview(stats, domains),
    ]

    for i, domain in enumerate(domains):
        key = f"domain_{i}"
        papers = domain_papers.get(key, [])
        sections.append(
            _render_monthly_domain(domain, key, papers, recommendations)
        )

    sections.append(
        f"\n---\n\n*Generated by arxiv-radar · {date.today().isoformat()}*\n"
    )

    report = "\n".join(sections)

    if output_dir is None:
        output_dir = Path(config.get("report_path", "~/arxiv-daily/")).expanduser() / "月报"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{year}-{month:02d}-月报.md"
    filepath = output_dir / filename
    filepath.write_text(report, encoding="utf-8")
    logger.info(f"Monthly report saved: {filepath}")

    return report


# ─────────────────────────── CLI ───────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")

    parser = argparse.ArgumentParser(description="Generate monthly arxiv report")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True)
    parser.add_argument("--config", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--force-refresh", action="store_true")
    args = parser.parse_args()

    skill_dir = Path(__file__).parent.parent
    cfg_path = args.config or skill_dir / "config.template.md"
    config = parse_config(cfg_path)
    domains = config["domains"]

    report = generate_monthly_report(
        args.year, args.month, config, domains,
        output_dir=args.output_dir,
        force_refresh=args.force_refresh,
    )
    print(report[:2000])
    print(f"\n... (total {len(report)} chars)")
