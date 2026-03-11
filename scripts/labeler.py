"""
labeler.py — Paper labeling for arxiv-radar.

Adds structured labels to papers:
  ⭐ VIP:{name}    — paper from a notable researcher
  🔓 open-source  — code available
  🏢 {Lab}        — paper from a major research lab
  📊 benchmark    — benchmark / dataset paper
  🔬 survey       — survey / review paper
"""

from __future__ import annotations
import re
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────── VIP Author Detection ───────────────────────────

def _normalize_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse spaces."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-zA-Z\s]", "", name)).strip().lower()


def _names_match(paper_author: str, vip_name: str, threshold: int = 85) -> bool:
    """
    Fuzzy match a paper author name against a VIP name.
    Uses token_sort_ratio to handle reordered name parts.
    Falls back to simple substring check if thefuzz is unavailable.
    """
    pa = _normalize_name(paper_author)
    vn = _normalize_name(vip_name)

    # Exact match first
    if pa == vn:
        return True

    # Last name exact match (important for Chinese researchers)
    vip_parts = vn.split()
    paper_parts = pa.split()
    if vip_parts and paper_parts:
        if vip_parts[-1] == paper_parts[-1]:
            # Same last name - check first initial
            if len(vip_parts[0]) >= 1 and len(paper_parts[0]) >= 1:
                if vip_parts[0][0] == paper_parts[0][0]:
                    return True

    try:
        from thefuzz import fuzz
        score = fuzz.token_sort_ratio(pa, vn)
        return score >= threshold
    except ImportError:
        # Simple fallback
        return pa in vn or vn in pa


def detect_vip_authors(
    paper: dict,
    vip_list: list[str],
) -> list[str]:
    """
    Check if any paper author matches the VIP list.
    Returns list of matched VIP names.
    """
    matched = []
    for author in paper.get("authors", []):
        for vip in vip_list:
            if _names_match(author, vip):
                matched.append(vip)
                break  # one match per author
    return matched


# ─────────────────────────── Open-source Detection ───────────────────────────

_GITHUB_PATTERN = re.compile(r"github\.com/[\w\-]+/[\w\-]+", re.IGNORECASE)
_OPENSOURCE_PHRASES = [
    "code available", "code is available", "open source", "open-source",
    "publicly available", "code released", "released publicly",
    "available at github", "source code", "implementation available",
    "available online", "we release", "we open-source",
]

def detect_open_source(paper: dict) -> bool:
    """Check if the paper mentions open-source code availability."""
    text = (paper.get("title", "") + " " + paper.get("abstract", "")).lower()
    if _GITHUB_PATTERN.search(text):
        return True
    return any(phrase in text for phrase in _OPENSOURCE_PHRASES)


# ─────────────────────────── Lab/Org Detection ───────────────────────────

_ORG_PATTERNS = {
    "Meta AI": ["meta ai", "facebook ai", "fair research", "meta research"],
    "Google DeepMind": ["google deepmind", "deepmind"],
    "Google Brain": ["google brain"],
    "Google Research": ["google research"],
    "OpenAI": ["openai"],
    "ByteDance": ["bytedance", "byteresearch"],
    "Microsoft Research": ["microsoft research", "msra"],
    "Apple Research": ["apple research", "apple inc"],
    "Tsinghua": ["tsinghua"],
    "PKU": ["peking university", "pku"],
    "CUHK": ["chinese university of hong kong", "cuhk"],
    "NTU": ["nanyang technological"],
}

def detect_labs(paper: dict, org_list: list[str]) -> list[str]:
    """Detect which research labs/organizations authored the paper."""
    # Check abstract + title for org mentions
    text = (paper.get("title", "") + " " + paper.get("abstract", "")).lower()
    detected = []

    for org in org_list:
        org_lower = org.lower()
        if org_lower in text:
            detected.append(f"🏢 {org}")
            continue

        # Check aliases
        aliases = _ORG_PATTERNS.get(org, [])
        if any(alias in text for alias in aliases):
            detected.append(f"🏢 {org}")

    return detected


# ─────────────────────────── Quality/Type Labels ───────────────────────────

def detect_paper_type(paper: dict) -> str:
    """
    Classify paper type: 方法文 / Benchmark / Survey / 其他

    Returns one of: "方法文", "Benchmark", "Survey", "其他"
    """
    text = (paper.get("title", "") + " " + paper.get("abstract", "")).lower()

    # Survey indicators
    survey_kws = [
        "survey", "review", "overview", "comprehensive study",
        "we review", "literature review", "systematic review",
        "we survey", "tutorial",
    ]
    if any(k in text for k in survey_kws):
        return "Survey"

    # Benchmark / Dataset indicators
    bench_kws = [
        "benchmark", "we propose a new dataset", "we collect",
        "we annotate", "new dataset", "large-scale dataset",
        "we introduce a dataset", "evaluation benchmark",
        "we build", "we construct a", "leaderboard",
        "we present a dataset", "we create a dataset",
    ]
    if any(k in text for k in bench_kws):
        return "Benchmark"

    # Default: 方法文
    return "方法文"


# ─────────────────────────── Main Labeler ───────────────────────────

def label_papers(
    papers: list[dict],
    vip_list: list[str],
    orgs: list[str],
) -> list[dict]:
    """
    Add labels to all papers in-place.

    Labels added to paper["labels"] (list of strings).
    """
    for paper in papers:
        labels = paper.get("labels", [])

        # VIP author labels
        matched_vips = detect_vip_authors(paper, vip_list)
        for vip in matched_vips:
            labels.append(f"⭐ VIP:{vip}")

        # Open source
        if detect_open_source(paper):
            labels.append("🔓 open-source")

        # Lab labels
        lab_labels = detect_labs(paper, orgs)
        labels.extend(lab_labels)

        # Paper type label
        paper_type = detect_paper_type(paper)
        if paper_type == "Benchmark":
            labels.append("📊 Benchmark")
        elif paper_type == "Survey":
            labels.append("🔬 Survey")
        else:
            labels.append("📝 方法文")

        paper["paper_type"] = paper_type
        paper["labels"] = labels

    total_vip = sum(1 for p in papers if any("VIP" in l for l in p["labels"]))
    total_os = sum(1 for p in papers if any("open-source" in l for l in p["labels"]))
    logger.info(
        f"Labeled {len(papers)} papers: {total_vip} VIP, {total_os} open-source"
    )
    return papers


# ─────────────────────────── CLI Test ───────────────────────────

if __name__ == "__main__":
    from pathlib import Path

    VIP_LIST = [
        "Kaiming He", "Saining Xie", "Ross Girshick", "Piotr Dollar",
        "Boyang Zheng", "Nanye Ma", "Shengbang Tong", "Yann LeCun",
    ]
    ORG_LIST = [
        "Meta AI", "Google Brain", "Google DeepMind", "OpenAI", "ByteDance",
        "Microsoft Research",
    ]

    test_papers = [
        {
            "id": "2406.07550", "title": "An Image is Worth 32 Tokens for Reconstruction and Generation",
            "abstract": "We propose TiTok, a compact 1D tokenizer. Code is available at github.com/bytedance/1d-tokenizer",
            "authors": ["Qihang Yu", "Mark Weber", "Xueqing Deng", "Xiaohui Shen", "Daniel Cremers", "Liang-Chieh Chen"],
            "labels": [],
        },
        {
            "id": "2510.11690", "title": "Diffusion Transformers with Representation Autoencoders",
            "abstract": "We propose Representation Autoencoders (RAEs). Experiments conducted at Meta AI Research show that RAEs significantly improve diffusion model quality.",
            "authors": ["Boyang Zheng", "Nanye Ma", "Shengbang Tong", "Saining Xie"],
            "labels": [],
        },
        {
            "id": "test_003", "title": "Deep Residual Learning for Image Recognition",
            "abstract": "We present deep residual networks from Microsoft Research. The open-source implementation is publicly available.",
            "authors": ["Kaiming He", "Xiangyu Zhang", "Shaoqing Ren", "Jian Sun"],
            "labels": [],
        },
        {
            "id": "test_004", "title": "A Generic Paper",
            "abstract": "This is a generic paper with no special labels.",
            "authors": ["Unknown Author"],
            "labels": [],
        },
    ]

    print("=== Labeler Standalone Test ===\n")
    labeled = label_papers(test_papers, VIP_LIST, ORG_LIST)

    for p in labeled:
        print(f"[{p['id']}] {p['title'][:60]}...")
        print(f"  Authors: {', '.join(p['authors'][:3])}")
        print(f"  Labels: {p['labels']}\n")

    # Assertions
    rae_paper = next(p for p in labeled if p["id"] == "2510.11690")
    assert any("VIP:Boyang Zheng" in l for l in rae_paper["labels"]), "Should detect Boyang Zheng as VIP"
    assert any("VIP:Saining Xie" in l for l in rae_paper["labels"]), "Should detect Saining Xie as VIP"

    resnet_paper = next(p for p in labeled if p["id"] == "test_003")
    assert any("VIP:Kaiming He" in l for l in resnet_paper["labels"]), "Should detect Kaiming He"
    assert any("open-source" in l for l in resnet_paper["labels"]), "Should detect open-source"
    assert any("Microsoft Research" in l for l in resnet_paper["labels"]), "Should detect MSR"

    titok_paper = next(p for p in labeled if p["id"] == "2406.07550")
    assert any("open-source" in l for l in titok_paper["labels"]), "Should detect github link"

    print("✅ All labeler assertions passed!")
