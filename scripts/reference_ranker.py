"""
reference_ranker.py — LLM-powered reference filtering and method variant tagging.

For each paper, uses wq/claude46 to:
  1. Rank references by method relevance (filter out theoretical/background refs)
  2. Extract method variant tags (HOW a baseline was used/modified)
  3. Classify reference relationship type

Output per reference:
  - relevance_rank: 1-20 (only top 20 kept)
  - rel_type: "baseline" | "method_source" | "technique_variant" | "extension"
  - variant_tag: e.g. "dino:semantic-alignment", "titok:causal-rewrite"
  - reason: one-line explanation
"""

from __future__ import annotations
import json
import logging
import subprocess
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

RANKER_PROMPT = """你是一个CV论文方法线索分析专家。

## 任务
给定一篇目标论文和它的引用列表，执行两个任务：

### 任务1: 引用筛选
从引用列表中选出 **最多20篇** 与目标论文的**方法/技术**直接相关的论文。
排除纯理论支撑（如Transformer原文、数据集论文、通用优化器论文等）。

### 任务2: 方法变体标注
对目标论文本身，标注它使用了哪些基础方法，以及**怎么改的**。

格式：`base_method:variant_approach`
示例：
- `dino:semantic-alignment` — 用DINO做语义对齐
- `dino:feature-distillation` — 用DINO做特征蒸馏
- `titok:causal-rewrite` — 将TiTok从双向改为因果
- `vqgan:multi-scale-codebook` — 多尺度码本
- `flow-matching:image-tokenization` — 用flow matching做图像tokenization
- `clip:generation-guidance` — 用CLIP引导生成

## 目标论文
标题: {title}
摘要: {abstract}

## 引用列表
{references}

## 输出格式 (严格JSON)
```json
{{
  "top_references": [
    {{
      "idx": 0,
      "title": "引用论文标题",
      "rel_type": "baseline|method_source|technique_variant|extension",
      "reason": "一句话说明与目标论文的方法关系"
    }}
  ],
  "method_variants": [
    {{
      "base_method": "dino",
      "variant_tag": "dino:semantic-alignment",
      "description": "使用DINO的语义特征进行对齐损失"
    }}
  ],
  "paper_method_summary": "一句话总结这篇论文的核心方法创新"
}}
```

只输出JSON，不要其他文字。"""


def _format_references(refs: list[dict], max_refs: int = 40) -> str:
    """Format reference list for LLM prompt."""
    lines = []
    for i, ref in enumerate(refs[:max_refs]):
        title = ref.get("title", "Unknown")
        abstract = ref.get("abstract", "")[:150] if ref.get("abstract") else ""
        lines.append(f"[{i}] {title}")
        if abstract:
            lines.append(f"    {abstract}...")
    return "\n".join(lines)


def _call_claude(prompt: str, timeout: int = 120) -> Optional[str]:
    """Call wq/claude46 via claude CLI for analysis."""
    try:
        result = subprocess.run(
            ["claude", "-p", "--model", "wq/claude46",
             "--output-format", "text"],
            input=prompt,
            capture_output=True, text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            logger.error(f"Claude CLI error: {result.stderr[:200]}")
            return None
    except FileNotFoundError:
        logger.warning("claude CLI not found, trying subprocess with openclaw...")
        return None
    except subprocess.TimeoutExpired:
        logger.error("Claude CLI timeout")
        return None
    except Exception as e:
        logger.error(f"Claude call failed: {e}")
        return None


def _parse_llm_response(response: str) -> Optional[dict]:
    """Extract JSON from LLM response."""
    if not response:
        return None
    # Find JSON block
    json_match = re.search(r'```json\s*\n(.*?)\n```', response, re.DOTALL)
    if json_match:
        text = json_match.group(1)
    else:
        # Try raw JSON
        text = response.strip()
        if text.startswith('{'):
            pass
        else:
            # Find first { to last }
            start = text.find('{')
            end = text.rfind('}')
            if start >= 0 and end > start:
                text = text[start:end+1]
            else:
                return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        return None


def rank_references(
    paper: dict,
    references: list[dict],
    use_llm: bool = True,
) -> dict:
    """
    Rank and filter references for a paper.

    Args:
        paper: target paper dict (needs title, abstract)
        references: list of reference paper dicts from S2
        use_llm: if True, use claude46; if False, use heuristic

    Returns:
        {
            "top_refs": [{"idx": int, "title": str, "rel_type": str, "reason": str}],
            "method_variants": [{"base_method": str, "variant_tag": str, "description": str}],
            "paper_method_summary": str
        }
    """
    if not references:
        return {"top_refs": [], "method_variants": [], "paper_method_summary": ""}

    if use_llm:
        prompt = RANKER_PROMPT.format(
            title=paper.get("title", ""),
            abstract=paper.get("abstract", "")[:800],
            references=_format_references(references, max_refs=40),
        )
        response = _call_claude(prompt)
        parsed = _parse_llm_response(response)
        if parsed:
            return {
                "top_refs": parsed.get("top_references", [])[:20],
                "method_variants": parsed.get("method_variants", []),
                "paper_method_summary": parsed.get("paper_method_summary", ""),
            }
        logger.warning(f"LLM ranking failed for {paper.get('id', '?')}, falling back to heuristic")

    # Heuristic fallback: keep refs with high citation count or matching keywords
    from baseline_extractor import CANONICAL_METHODS
    method_names = set(CANONICAL_METHODS.keys())

    scored = []
    for i, ref in enumerate(references):
        score = 0
        title_lower = (ref.get("title", "") or "").lower()
        # Boost if title contains a known method name
        for method in method_names:
            if method in title_lower:
                score += 10
        # Boost highly cited refs
        cite_count = ref.get("citationCount", 0) or 0
        if cite_count > 100:
            score += 3
        elif cite_count > 50:
            score += 2
        # Penalize very generic titles
        generic = ["survey", "review", "dataset", "benchmark", "tutorial", "introduction"]
        if any(g in title_lower for g in generic):
            score -= 5
        scored.append((i, ref, score))

    scored.sort(key=lambda x: -x[2])
    top = scored[:20]

    return {
        "top_refs": [
            {"idx": i, "title": ref.get("title", ""), "rel_type": "baseline", "reason": "heuristic"}
            for i, ref, _ in top
        ],
        "method_variants": [],
        "paper_method_summary": "",
    }


def rank_references_batch(
    papers_with_refs: list[tuple[dict, list[dict]]],
    use_llm: bool = True,
    batch_size: int = 5,
) -> dict:
    """
    Batch rank references for multiple papers.

    Args:
        papers_with_refs: list of (paper, references) tuples
        use_llm: use LLM for ranking
        batch_size: papers per LLM call (for future batching)

    Returns:
        {paper_id: ranking_result}
    """
    results = {}
    for i, (paper, refs) in enumerate(papers_with_refs):
        pid = paper.get("id", f"unknown_{i}")
        logger.info(f"Ranking refs for [{i+1}/{len(papers_with_refs)}] {paper.get('title', '')[:50]}...")
        result = rank_references(paper, refs, use_llm=use_llm)
        results[pid] = result
        logger.info(f"  → {len(result['top_refs'])} top refs, {len(result['method_variants'])} variants")
    return results


def store_method_variants(paper_id: str, variants: list[dict], db) -> int:
    """Store method variant tags in the DB via PaperDB."""
    try:
        return db.store_method_variants(paper_id, variants)
    except Exception as e:
        logger.warning(f"store_method_variants fallback: {e}")
        return 0


def get_exploration_branches(db, min_papers: int = 2) -> list[dict]:
    """Detect method exploration branches via PaperDB."""
    try:
        return db.get_exploration_branches(min_papers)
    except Exception as e:
        logger.warning(f"get_exploration_branches error: {e}")
        return []


def format_exploration_branches(branches: list[dict]) -> str:
    """Format exploration branches as markdown for reports."""
    if not branches:
        return ""

    lines = [
        "\n---\n",
        "## 🔬 方法探索分支",
        "",
        "> 同一基础方法的不同变体探索——揭示研究社区正在集中攻关的技术方向",
        "",
    ]

    for branch in branches[:6]:
        base = branch["base_method"]
        n = branch["paper_count"]
        lines.append(f"### `{base}` ({n} 篇论文, {branch['variant_count']} 种变体)")
        lines.append("")

        for p in branch["papers"][:5]:
            tag = p["variant_tag"].split(":", 1)[-1] if ":" in p["variant_tag"] else p["variant_tag"]
            desc = p["description"][:80] if p["description"] else ""
            date = p["date"][:7] if p["date"] else ""
            lines.append(f"- **{tag}** — {p['title']} ({date})")
            if desc:
                lines.append(f"  > {desc}")
        lines.append("")

    return "\n".join(lines)


# ─────────────────────── CLI Test ───────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")

    # Test heuristic ranking (no LLM)
    paper = {
        "id": "2603.06449v1",
        "title": "CaTok: Taming Mean Flows for One-Dimensional Causal Image Tokenization",
        "abstract": "We present CaTok, a novel approach that leverages mean flow matching for causal 1D image tokenization. Unlike TiTok which uses bidirectional attention, CaTok enables autoregressive generation by design. We compare against VQGAN, TiTok, LlamaGen and achieve state-of-the-art FID on ImageNet.",
    }
    refs = [
        {"title": "TiTok: An Image is Worth 32 Tokens", "citationCount": 225, "abstract": "1D image tokenizer"},
        {"title": "Attention Is All You Need", "citationCount": 100000, "abstract": "transformer architecture"},
        {"title": "VQGAN: Taming Transformers", "citationCount": 3000, "abstract": "vector quantized GAN"},
        {"title": "ImageNet Large Scale Visual Recognition", "citationCount": 50000, "abstract": "dataset"},
        {"title": "LlamaGen: Autoregressive Image Generation", "citationCount": 611, "abstract": "AR image generation"},
        {"title": "Flow Matching for Generative Modeling", "citationCount": 500, "abstract": "flow matching framework"},
        {"title": "Adam: A Method for Stochastic Optimization", "citationCount": 80000, "abstract": "optimizer"},
        {"title": "MaskGIT: Masked Generative Image Transformer", "citationCount": 1049, "abstract": "masked image generation"},
    ]

    print("=== Heuristic Ranking Test ===\n")
    result = rank_references(paper, refs, use_llm=False)
    for ref in result["top_refs"][:5]:
        print(f"  [{ref['idx']}] {ref['title'][:50]}")
    print(f"\nTop refs: {len(result['top_refs'])}")

    # Test method variant storage
    import tempfile, sys
    sys.path.insert(0, str(Path(__file__).parent))
    from paper_db import PaperDB

    db = PaperDB(tempfile.mktemp(suffix=".db"))
    db.upsert_paper(paper)

    variants = [
        {"base_method": "titok", "variant_tag": "titok:causal-rewrite", "description": "将TiTok的双向attention改为因果式"},
        {"base_method": "flow-matching", "variant_tag": "flow-matching:image-tokenization", "description": "用mean flow matching学习图像token"},
    ]
    n = store_method_variants("2603.06449v1", variants, db)
    print(f"\nStored {n} method variants")

    # Test exploration branch detection
    # Add another paper with titok variant
    db.upsert_paper({"id": "2603.99999", "title": "FastTok: Speed TiTok", "best_score": 0.5,
                      "date": "2026-03-08", "domain": "1D", "paper_type": "方法文"})
    store_method_variants("2603.99999", [
        {"base_method": "titok", "variant_tag": "titok:pruned-codebook", "description": "裁剪码本加速推理"},
    ], db)

    branches = get_exploration_branches(db, min_papers=2)
    print(f"\nExploration branches: {len(branches)}")
    if branches:
        md = format_exploration_branches(branches)
        print(md)

    print("\n✅ Reference ranker test complete!")
