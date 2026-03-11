"""
reporter.py — Daily report generator for arxiv-radar.

Generates a clean, Discord-friendly markdown daily briefing with:
  - Stats header
  - Must-read papers with full analysis
  - Full filtered pool per domain
  - No markdown tables (Discord-friendly)
"""

from __future__ import annotations
import os
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────── Formatting Helpers ───────────────────────────

def _format_labels(labels: list[str]) -> str:
    """Format labels as a compact inline string."""
    if not labels:
        return ""
    return " · ".join(labels)


def _format_authors(authors: list[str], max_n: int = 4) -> str:
    """Format author list, truncating if too long."""
    if len(authors) <= max_n:
        return ", ".join(authors)
    return ", ".join(authors[:max_n]) + f" et al. ({len(authors)} authors)"


def _format_abstract_snippet(abstract: str, max_chars: int = 280) -> str:
    """Trim abstract to a readable snippet."""
    abstract = abstract.strip()
    if len(abstract) <= max_chars:
        return abstract
    # Cut at a sentence boundary
    trimmed = abstract[:max_chars]
    last_period = trimmed.rfind(". ")
    if last_period > max_chars * 0.6:
        return trimmed[:last_period + 1]
    return trimmed + "..."


def _domain_emoji(domain_name: str) -> str:
    """Pick an emoji for each domain."""
    name_lower = domain_name.lower()
    if "token" in name_lower or "1d" in name_lower:
        return "🔢"
    if "unified" in name_lower or "multimodal" in name_lower:
        return "🔗"
    if "diffusion" in name_lower or "generat" in name_lower:
        return "🎨"
    return "🔬"


# ─────────────────────────── Report Sections ───────────────────────────

def _render_stats(
    total_crawled: int,
    after_noise: int,
    domain_counts: dict,
    report_date: str,
) -> str:
    lines = [
        f"# 🔬 arxiv Daily Report — {report_date}",
        "",
        "## 📊 Today's Numbers",
        f"- 📥 Papers crawled: **{total_crawled}**",
        f"- 🧹 After noise filter: **{after_noise}**",
        f"- ✅ Relevant papers kept: **{sum(domain_counts.values())}**",
    ]
    for domain_name, count in domain_counts.items():
        lines.append(f"  - {domain_name}: **{count}**")

    return "\n".join(lines)


def _render_must_reads(recommendations: dict) -> str:
    """Render the must-read section."""
    lines = ["", "---", "", "## ⭐ Must-Read Papers"]

    has_any = False
    for domain_key in sorted(recommendations.keys()):
        domain_result = recommendations[domain_key]
        domain_name = domain_result.get("name", domain_key)
        recs = domain_result.get("recommendations", [])

        if not recs:
            continue
        has_any = True

        emoji = _domain_emoji(domain_name)
        lines.append(f"\n### {emoji} {domain_name}")

        for rec in recs:
            paper = rec["paper"]
            rank = rec["rank"]
            score = rec["score"]
            why_read = rec.get("why_read", "")
            labels = paper.get("labels", [])
            authors = paper.get("authors", [])

            lines.append(f"\n**{rank}. [{paper['title']}]({paper['arxiv_url']})**")
            lines.append(f"👤 {_format_authors(authors)}")

            if labels:
                lines.append(f"🏷️ {_format_labels(labels)}")

            lines.append(f"📈 Relevance score: {score:.3f}")

            if why_read:
                lines.append(f"\n💡 **Why read:** {why_read}")

            snippet = _format_abstract_snippet(paper.get("abstract", ""), 300)
            if snippet:
                lines.append(f"\n> {snippet}")

    if not has_any:
        lines.append("\n*No must-read papers identified today.*")

    return "\n".join(lines)


def _render_full_pool(filter_result: dict, domains: list[dict]) -> str:
    """Render the full filtered paper pool section."""
    lines = ["", "---", "", "## 📚 Full Filtered Pool"]

    for i, domain in enumerate(domains):
        domain_key = f"domain_{i}"
        papers = filter_result.get(domain_key, [])
        domain_name = domain["name"]
        emoji = _domain_emoji(domain_name)

        lines.append(f"\n### {emoji} {domain_name} ({len(papers)} papers)")

        if not papers:
            lines.append("*No relevant papers today.*")
            continue

        # Sort by score descending
        papers_sorted = sorted(
            papers,
            key=lambda p: p.get("similarity_scores", {}).get(domain_name, 0),
            reverse=True,
        )

        for paper in papers_sorted:
            score = paper.get("similarity_scores", {}).get(domain_name, 0)
            authors = _format_authors(paper.get("authors", []), 3)
            labels = paper.get("labels", [])
            label_str = f" | {_format_labels(labels)}" if labels else ""

            lines.append(
                f"- [{paper['title'][:80]}]({paper['arxiv_url']}) "
                f"| {authors} | 📈{score:.2f}{label_str}"
            )

    return "\n".join(lines)


def _render_footer(config_snapshot: dict | None = None) -> str:
    lines = [
        "",
        "---",
        "",
        "## ⚙️ Config Snapshot",
    ]
    if config_snapshot:
        lines.append(f"- Model: `{config_snapshot.get('embedding_model', 'all-MiniLM-L6-v2')}`")
        lines.append(f"- Threshold mode: `{config_snapshot.get('threshold_mode', 'adaptive')}`")
        lines.append(f"- Threshold: `{config_snapshot.get('similarity_threshold', 0.35)}`")
        lines.append(f"- Top-K per domain: `{config_snapshot.get('adaptive_top_k', 30)}`")
        lines.append(f"- Categories: `{', '.join(config_snapshot.get('arxiv_categories', ['cs.CV']))}`")
    lines.append(
        f"\n*Generated by arxiv-radar on {datetime.now().strftime('%Y-%m-%d %H:%M')}*"
    )
    return "\n".join(lines)


# ─────────────────────────── Main Report Generator ───────────────────────────

def generate_report(
    all_papers: list[dict],
    filter_result: dict,
    recommendations: dict,
    domains: list[dict],
    config: dict | None = None,
    report_date: str | None = None,
) -> str:
    """
    Generate the full daily report as a markdown string.

    Args:
        all_papers: All crawled papers
        filter_result: Output from filter_papers()
        recommendations: Output from recommend()
        domains: Domain definitions
        config: Parsed config (for footer snapshot)
        report_date: Date string (default: today)
    """
    if report_date is None:
        report_date = date.today().isoformat()

    stats = filter_result.get("stats", {})
    total_crawled = stats.get("total_input", len(all_papers))
    after_noise = stats.get("after_noise_filter", total_crawled)

    domain_counts = {
        domain["name"]: len(filter_result.get(f"domain_{i}", []))
        for i, domain in enumerate(domains)
    }

    sections = [
        _render_stats(total_crawled, after_noise, domain_counts, report_date),
        _render_must_reads(recommendations),
        _render_full_pool(filter_result, domains),
        _render_footer(config),
    ]

    return "\n".join(sections)


def save_report(report: str, config: dict, report_date: str | None = None) -> str:
    """Save the report to a file. Returns the file path."""
    if report_date is None:
        report_date = date.today().isoformat()

    output_mode = config.get("report_output", "file")

    if output_mode == "stdout":
        print(report)
        return ""

    report_dir = Path(config.get("report_path", "~/arxiv-daily/")).expanduser() / "日报"
    report_dir.mkdir(parents=True, exist_ok=True)

    filename = f"arxiv-report-{report_date}.md"
    filepath = report_dir / filename
    filepath.write_text(report, encoding="utf-8")

    logger.info(f"Report saved to {filepath}")
    return str(filepath)


# ─────────────────────────── CLI Test ───────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Mock data
    mock_domains = [
        {"name": "1D Image Tokenizer"},
        {"name": "Unified Understanding & Generation"},
    ]

    mock_filter_result = {
        "domain_0": [
            {"id": "2406.07550", "title": "An Image is Worth 32 Tokens for Reconstruction and Generation",
             "abstract": "We propose TiTok, a compact 1D tokenizer leveraging region redundancy to represent an image with only 32 tokens. The model achieves state-of-the-art generation performance while being substantially faster.",
             "authors": ["Qihang Yu", "Mark Weber"], "arxiv_url": "https://arxiv.org/abs/2406.07550",
             "labels": ["🔓 open-source"], "similarity_scores": {"1D Image Tokenizer": 0.89}},
            {"id": "test_002", "title": "Discrete Visual Tokens for High-Quality Image Synthesis",
             "abstract": "A compact visual tokenizer using region redundancy for state-of-the-art generation.",
             "authors": ["Bob Chen"], "arxiv_url": "https://arxiv.org/abs/test_002",
             "labels": [], "similarity_scores": {"1D Image Tokenizer": 0.72}},
        ],
        "domain_1": [
            {"id": "2510.11690", "title": "Diffusion Transformers with Representation Autoencoders",
             "abstract": "We propose RAEs, a new approach that leverages pretrained visual encoders for diffusion. Experiments at Meta AI show significant quality improvements.",
             "authors": ["Boyang Zheng", "Saining Xie"], "arxiv_url": "https://arxiv.org/abs/2510.11690",
             "labels": ["⭐ VIP:Boyang Zheng", "⭐ VIP:Saining Xie", "🏢 Meta AI"],
             "similarity_scores": {"Unified Understanding & Generation": 0.81}},
        ],
        "unmatched": [],
        "rejected_noise": [{"id": "noise_001", "title": "Medical Image Segmentation"}],
        "stats": {
            "total_input": 250,
            "after_noise_filter": 180,
            "noise_rejected": 70,
            "total_filtered": 3,
        },
    }

    mock_recommendations = {
        "domain_0": {
            "name": "1D Image Tokenizer",
            "recommendations": [
                {
                    "rank": 1,
                    "paper": mock_filter_result["domain_0"][0],
                    "score": 0.94,
                    "why_read": "Introduces a breakthrough 1D image tokenization approach that compresses images to just 32 tokens, enabling much faster autoregressive generation while maintaining quality.",
                },
            ],
        },
        "domain_1": {
            "name": "Unified Understanding & Generation",
            "recommendations": [
                {
                    "rank": 1,
                    "paper": mock_filter_result["domain_1"][0],
                    "score": 0.91,
                    "why_read": "Demonstrates that pretrained visual encoders (designed for understanding) can serve as powerful representation backbones for diffusion generation, bridging the understanding-generation gap directly relevant to unified representation research.",
                },
            ],
        },
    }

    mock_config = {
        "embedding_model": "all-MiniLM-L6-v2",
        "threshold_mode": "adaptive",
        "similarity_threshold": 0.35,
        "adaptive_top_k": 30,
        "arxiv_categories": ["cs.CV", "cs.LG", "cs.AI"],
    }

    print("=== Reporter Standalone Test ===\n")
    report = generate_report(
        all_papers=[],
        filter_result=mock_filter_result,
        recommendations=mock_recommendations,
        domains=mock_domains,
        config=mock_config,
    )

    print(report)
    print("\n✅ Reporter test complete!")

    # Save to temp file
    import tempfile, os
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(report)
        print(f"\n📄 Report saved to: {f.name}")
