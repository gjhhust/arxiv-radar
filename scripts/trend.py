"""
trend.py — Keyword frequency tracking and trend detection.

Analyzes paper titles/abstracts over time to detect:
  - Rising topics (keyword frequency increasing week-over-week)
  - Dominant methods in recent window
  - Emerging patterns not seen in prior weeks

Output: trend report section for weekly/monthly reports.
"""

from __future__ import annotations
import json
import logging
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# CV/ML keywords to track (add more as needed)
TRACKED_KEYWORDS = [
    # Architectures
    "transformer", "diffusion", "autoregressive", "flow matching", "consistency model",
    "mamba", "state space", "linear attention",
    # Tasks/paradigms
    "tokenizer", "tokenization", "codebook", "vector quantization", "vq",
    "unified", "any-to-any", "multimodal", "vision-language",
    "understanding", "generation", "editing",
    # Methods
    "reinforcement learning", "rlhf", "dpo", "reward model",
    "knowledge distillation", "pruning", "quantization",
    "in-context learning", "chain of thought", "reasoning",
    # Visual
    "image generation", "video generation", "3d generation",
    "super resolution", "image restoration", "segmentation",
    "object detection", "depth estimation",
    # Scale/efficiency
    "efficient", "lightweight", "real-time", "inference acceleration",
    "token pruning", "token compression",
]


def extract_keywords_from_papers(papers: list[dict]) -> Counter:
    """Count keyword occurrences across a set of papers."""
    counts = Counter()
    for paper in papers:
        text = (paper.get("title", "") + " " + paper.get("abstract", "")).lower()
        for kw in TRACKED_KEYWORDS:
            if kw in text:
                counts[kw] += 1
    return counts


def compute_trends(
    current_papers: list[dict],
    historical_papers: list[dict] | None = None,
    top_n: int = 10,
) -> dict:
    """
    Compute keyword trends between current and historical period.

    Args:
        current_papers: papers from current window (e.g., this week)
        historical_papers: papers from prior window (e.g., last week)
        top_n: number of top keywords to report

    Returns:
        trend dict with rising/falling/new/top keywords
    """
    current_counts = extract_keywords_from_papers(current_papers)
    hist_counts = extract_keywords_from_papers(historical_papers or [])

    n_current = max(len(current_papers), 1)
    n_hist = max(len(historical_papers or []), 1)

    trends = {
        "top": [],        # Most frequent this period
        "rising": [],     # Frequency increased significantly
        "new": [],        # Appeared this period, not before
        "falling": [],    # Decreased significantly
    }

    # Top keywords this period
    for kw, cnt in current_counts.most_common(top_n):
        freq = cnt / n_current
        hist_freq = hist_counts.get(kw, 0) / n_hist
        delta = freq - hist_freq
        trends["top"].append({
            "keyword": kw,
            "count": cnt,
            "freq_pct": round(freq * 100, 1),
            "delta_pct": round(delta * 100, 1),
        })

    # Rising: freq increased by >5% and appeared >3 times
    for kw, cnt in current_counts.items():
        freq = cnt / n_current
        hist_freq = hist_counts.get(kw, 0) / n_hist
        delta = freq - hist_freq
        if delta > 0.05 and cnt >= 3:
            trends["rising"].append({
                "keyword": kw,
                "count": cnt,
                "delta_pct": round(delta * 100, 1),
                "prev_count": hist_counts.get(kw, 0),
            })

    # New: appeared this period but not before
    for kw, cnt in current_counts.items():
        if kw not in hist_counts and cnt >= 2:
            trends["new"].append({"keyword": kw, "count": cnt})

    # Sort
    trends["rising"].sort(key=lambda x: -x["delta_pct"])
    trends["new"].sort(key=lambda x: -x["count"])

    return trends


def format_trend_section(trends: dict, period_label: str = "本周") -> str:
    """Format trend analysis as a markdown section for weekly/monthly reports."""
    lines = [
        "\n---\n",
        "## 📈 趋势雷达",
        "",
        f"> 基于{period_label} {sum(t['count'] for t in trends['top'][:5])} 篇相关论文的关键词统计",
        "",
    ]

    # Top keywords
    if trends["top"]:
        lines.append("### 🔥 热门关键词")
        lines.append("")
        for t in trends["top"][:8]:
            bar = "█" * min(int(t["freq_pct"] / 2), 20)
            delta_str = ""
            if t.get("delta_pct", 0) > 2:
                delta_str = f" ⬆️ +{t['delta_pct']}%"
            elif t.get("delta_pct", 0) < -2:
                delta_str = f" ⬇️ {t['delta_pct']}%"
            lines.append(f"- `{t['keyword']}` {bar} {t['freq_pct']}% ({t['count']}篇){delta_str}")
        lines.append("")

    # Rising trends
    if trends["rising"]:
        lines.append("### ⬆️ 上升趋势")
        lines.append("")
        for t in trends["rising"][:5]:
            lines.append(f"- **{t['keyword']}**: {t['count']} 篇 (+{t['delta_pct']}% vs 上期, 上期 {t['prev_count']} 篇)")
        lines.append("")

    # New keywords
    if trends["new"]:
        lines.append("### 🆕 新出现")
        lines.append("")
        new_str = " · ".join(f"`{t['keyword']}` ({t['count']})" for t in trends["new"][:6])
        lines.append(f"{new_str}")
        lines.append("")

    return "\n".join(lines)


def generate_idea_seeds(
    domain_papers: dict,
    domains: list[dict],
    db=None,
    n_ideas: int = 4,
) -> str:
    """
    Generate idea seed prompts from cross-domain paper patterns.
    Uses heuristic cross-domain pairing + method gap detection.

    Returns:
        Markdown section with idea seeds
    """
    lines = [
        "\n---\n",
        "## 🧪 Idea Seeds",
        "",
        "> 基于本周论文图谱自动涌现的研究方向种子（供参考，需要你判断可行性）",
        "",
    ]

    # Gather top papers from each domain
    domain_tops = {}
    for i, domain in enumerate(domains):
        key = f"domain_{i}"
        papers = domain_papers.get(key, [])[:5]
        domain_tops[domain["name"]] = papers

    domain_names = list(domain_tops.keys())

    # Idea patterns
    ideas = []

    # Pattern 1: Cross-domain method transfer
    if len(domain_names) >= 2:
        d1, d2 = domain_names[0], domain_names[1]
        tops_d1 = domain_tops[d1][:2]
        tops_d2 = domain_tops[d2][:2]
        if tops_d1 and tops_d2:
            t1 = tops_d1[0]["title"][:45]
            t2 = tops_d2[0]["title"][:45]
            ideas.append({
                "title": "跨领域方法迁移",
                "description": f"将「{d1}」的方案（如 *{t1}*）引入到「{d2}」框架中——这两条方法线是否有尚未探索的结合点？",
                "source": f"{t1} × {t2}",
                "type": "方法迁移 🔀",
            })

    # Pattern 2: Efficiency vs Quality trade-off
    efficient_papers = []
    quality_papers = []
    for papers in domain_tops.values():
        for p in papers:
            text = (p.get("title", "") + p.get("abstract", "")).lower()
            if any(k in text for k in ["efficient", "lightweight", "real-time", "pruning", "fast"]):
                efficient_papers.append(p)
            elif any(k in text for k in ["generation", "quality", "fidelity", "perceptual"]):
                quality_papers.append(p)

    if efficient_papers and quality_papers:
        ideas.append({
            "title": "效率-质量平衡点探索",
            "description": f"本周出现了 {len(efficient_papers)} 篇效率优化和 {len(quality_papers)} 篇质量提升论文——在你关注的领域，是否存在一个统一框架能同时兼顾？",
            "source": f"*{efficient_papers[0]['title'][:40]}* + *{quality_papers[0]['title'][:40]}*",
            "type": "方法综合 🔧",
        })

    # Pattern 3: Benchmark gap
    benchmark_papers = []
    for papers in domain_tops.values():
        for p in papers:
            if p.get("paper_type") == "Benchmark":
                benchmark_papers.append(p)
    if benchmark_papers:
        ideas.append({
            "title": "Benchmark 驱动研究",
            "description": f"本周出现新 Benchmark「{benchmark_papers[0]['title'][:50]}」——这个评测设置是否揭示了现有方法的盲区？",
            "source": benchmark_papers[0]["title"][:60],
            "type": "评测驱动 📊",
        })

    # Pattern 4: Rising topic
    all_papers_flat = [p for ps in domain_tops.values() for p in ps]
    trends = compute_trends(all_papers_flat)
    if trends["rising"]:
        rising_kw = trends["rising"][0]["keyword"]
        ideas.append({
            "title": f"新兴方向: {rising_kw}",
            "description": f"「{rising_kw}」本周热度显著上升——是否有机会将其引入你的研究框架中？",
            "source": f"趋势分析（↑{trends['rising'][0]['delta_pct']}%）",
            "type": "趋势捕捉 📡",
        })

    # Format
    if not ideas:
        lines.append("*本周暂无自动涌现的 idea seeds，建议手动对比本周 top 论文。*")
    else:
        for i, idea in enumerate(ideas[:n_ideas], 1):
            lines.append(f"### {i}. {idea['title']} {idea['type']}")
            lines.append("")
            lines.append(f"{idea['description']}")
            lines.append(f"> 来源灵感: {idea['source']}")
            lines.append("")

    return "\n".join(lines)


# ─────────────────────── CLI Test ───────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")

    current = [
        {"title": "CaTok: Taming Mean Flows for Causal Image Tokenization",
         "abstract": "autoregressive tokenizer with flow matching for efficient video generation"},
        {"title": "FastSTAR: Token Pruning for Autoregressive Video Synthesis",
         "abstract": "efficient token pruning to accelerate autoregressive image and video generation"},
        {"title": "UniG2U-Bench: Unified Understanding and Generation Benchmark",
         "abstract": "benchmark for unified multimodal understanding and generation models"},
        {"title": "DREAM: Visual Understanding meets Text-to-Image Generation",
         "abstract": "unified framework for multimodal understanding and generation with reinforcement learning"},
        {"title": "iGVLM: Dynamic Vision Encoding for Multimodal Models",
         "abstract": "efficient vision-language model with dynamic token compression"},
    ]
    historical = [
        {"title": "TiTok: Image Tokenizer", "abstract": "vector quantization codebook for image generation"},
        {"title": "LlamaGen: Autoregressive Image Generation", "abstract": "autoregressive language model for image generation"},
    ]

    trends = compute_trends(current, historical)
    print("=== Trend Analysis ===")
    section = format_trend_section(trends)
    print(section)

    print("=== Idea Seeds ===")
    from config_parser import parse_config
    from pathlib import Path
    config = parse_config(Path(__file__).parent.parent / "config.template.md")
    domains = config["domains"]
    domain_papers = {
        "domain_0": current[:2],
        "domain_1": current[2:],
    }
    ideas_section = generate_idea_seeds(domain_papers, domains)
    print(ideas_section)
    print("✅ Trend + Idea Seeds test complete!")
