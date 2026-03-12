"""
paper_analyst.py — Scheme B "Unified Analyst" for arxiv-radar v3.0 A/B test.

Single LLM call with full context:
  Input:  paper title + abstract + top-15 references (title + first 150 chars of abstract)
  Output: cn_oneliner, cn_abstract, contribution_type, editorial_note,
          why_read, method_variants, key_refs

One larger, context-rich prompt vs Scheme A's two focused ones.
Failure mode: all-or-nothing (no partial output on parse failure).

Uses urllib.request (stdlib only, no requests dependency).
"""

from __future__ import annotations
import json
import logging
import os
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─────────────────── API config ───────────────────

BASE_URL    = os.environ.get("OPENAI_BASE_URL", "http://localhost:4141")
API_KEY     = os.environ.get("OPENAI_API_KEY", "test")
ANTHROPIC_MODELS = {"claude46","claude45","glm5","katcoder","kimik25","minimaxm21","minimaxm25","glm47"}

# ─────────────────── Unified prompt ───────────────────

UNIFIED_PROMPT_TEMPLATE = """\
你是一名计算机视觉领域的资深论文评审人，同时也是面向研究者的技术编辑。

## 任务
对下面这篇论文进行全面分析，**一次性**输出所有分析结果。

## 目标论文
标题: {title}
摘要: {abstract}

## Top-15 参考文献（标题 + 摘要前150字）
{ref_block}

---

## 输出要求
严格返回以下 JSON 对象（不要输出任何其他文字）：

{{
  "cn_oneliner": "<≤40字通俗说明，核心贡献，像发微博一样简明>",
  "cn_abstract": "<2-4句中文技术摘要，保留关键术语英文>",
  "contribution_type": "<incremental|significant|story-heavy|foundational>",
  "editorial_note": "<1-2句编辑判断，评价方法创新性、跨域价值、潜在影响>",
  "why_read": "<1句推荐理由，直接告诉读者这篇值不值得读以及为什么>",
  "method_variants": [
    {{
      "base_method": "<基础方法名，小写，如 titok / dino / flow-matching>",
      "variant_tag": "<base_method:variant-approach，如 titok:causal-rewrite>",
      "description": "<一句话说明如何改造了基础方法>"
    }}
  ],
  "key_refs": [
    {{
      "title": "<参考文献标题（与上方列表一致）>",
      "stance": "<extends|contrasts|uses|supports|mentions>",
      "note": "<一句话说明与本文的具体关系>"
    }}
  ]
}}

contribution_type 定义：
- incremental   — 在已有方法上小幅改进，实验充分但创新有限
- significant   — 有实质性方法创新或成功跨任务/跨模态推广
- story-heavy   — 工程为主，叙事包装过度，方法贡献有限
- foundational  — 开创性工作，方法范式改变，影响深远

stance 定义（key_refs）：
- extends   — 本文在该引用基础上直接扩展/修改
- contrasts — 本文拿该引用作对比 baseline
- uses      — 本文使用该引用的技术/框架/数据
- supports  — 该引用为本文提供理论/实验支撑
- mentions  — 仅简单提及，关系较弱

只输出 JSON，不要 markdown 代码块，不要其他文字。
"""


def _format_ref_block(refs: list[dict], max_refs: int = 15) -> str:
    """Format top-N references with title + 150-char abstract snippet."""
    lines = []
    for i, ref in enumerate(refs[:max_refs]):
        title    = ref.get("title", "Unknown")
        abstract = ref.get("abstract", "") or ""
        snippet  = abstract[:150].strip()
        if snippet:
            lines.append(f"[{i+1}] {title}\n    {snippet}...")
        else:
            lines.append(f"[{i+1}] {title}")
    return "\n".join(lines) if lines else "(no references available)"


def _llm_call(messages: list[dict], model: str, timeout: int = 120) -> tuple[str, float]:
    """
    Call wanqing-proxy at localhost:4141.
    - Anthropic models (claude46, glm5, etc.) → POST /messages
    - OpenAI models (gpt52, deepseekv32) → POST /oai/chat/completions
    Returns (content_str, latency_s).
    """
    model_id = model.split("/")[-1] if "/" in model else model
    is_anthropic = model_id in ANTHROPIC_MODELS

    if is_anthropic:
        url = BASE_URL.rstrip("/") + "/messages"
        payload = json.dumps({
            "model": model_id,
            "messages": messages,
            "max_tokens": 8192,  # thinking models need high budget (thinking + response)
        }).encode("utf-8")
    else:
        url = BASE_URL.rstrip("/") + "/oai/chat/completions"
        payload = json.dumps({
            "model": model_id,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 3000,
        }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
    )

    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    latency = time.time() - t0

    data = json.loads(raw)
    if is_anthropic:
        # Find the text block (skip thinking blocks if present)
        content_blocks = data.get("content", [])
        text_content = next(
            (b["text"] for b in content_blocks if b.get("type") == "text"),
            None
        )
        if text_content is None:
            raise ValueError(f"No text block in response: {content_blocks[:1]}")
        content = text_content.strip()
    else:
        content = data["choices"][0]["message"]["content"].strip()
    return content, latency


def _parse_json_from_text(text: str) -> tuple[dict | None, str | None]:
    """
    Extract a JSON object from LLM output text.

    Returns (parsed_dict, error_msg). error_msg is None on success.
    """
    text = text.strip()

    # Strip markdown code fences if present
    fenced = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    # Direct parse
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        pass

    # Find outermost {...}
    start = text.find("{")
    end   = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1]), None
        except json.JSONDecodeError as e:
            return None, f"JSONDecodeError after extraction: {e}"

    return None, f"No JSON object found ({len(text)} chars)"


def analyze_paper_scheme_b(paper: dict, refs: list, model: str = "wq/claude46") -> dict:
    """
    Single unified LLM call for comprehensive paper analysis (Scheme B).

    Combines Chinese summary, editorial analysis, method variants, and citation
    stance classification into one large context-rich prompt.

    Args:
        paper: paper dict with at least 'title' and 'abstract'
        refs:  list of reference paper dicts (top-15 used; needs 'title', optionally 'abstract')
        model: LLM model alias (e.g. "wq/claude46")

    Returns:
        Dict with all analysis fields plus latency_s and parse_errors:
          cn_oneliner, cn_abstract, contribution_type, editorial_note,
          why_read, method_variants, key_refs, latency_s, parse_errors
    """
    title    = paper.get("title", "")
    abstract = paper.get("abstract", "")[:800]
    abstract = abstract.replace('"', "'").replace('\u201c', "'").replace('\u201d', "'")

    ref_block  = _format_ref_block(refs, max_refs=15)
    prompt     = UNIFIED_PROMPT_TEMPLATE.format(
        title=title,
        abstract=abstract,
        ref_block=ref_block,
    )

    parse_errors: list[str] = []
    latency = 0.0

    # Default empty values
    output: dict[str, Any] = {
        "cn_oneliner":       "",
        "cn_abstract":       "",
        "contribution_type": "",
        "editorial_note":    "",
        "why_read":          "",
        "method_variants":   [],
        "key_refs":          [],
    }

    try:
        raw, latency = _llm_call(
            [{"role": "user", "content": prompt}],
            model=model,
        )
        parsed, err = _parse_json_from_text(raw)
        if err:
            parse_errors.append(f"parse: {err}")
            logger.warning(f"[scheme_b] Parse error for {paper.get('id', '?')}: {err}")
        else:
            # Merge parsed fields into output (only expected keys)
            for key in output:
                if key in parsed:
                    output[key] = parsed[key]
    except Exception as e:
        parse_errors.append(f"call_error: {e}")
        logger.error(f"[scheme_b] LLM error for {paper.get('id', '?')}: {e}")

    output["latency_s"]    = round(latency, 3)
    output["parse_errors"] = parse_errors
    return output


# ─────────────────── CLI test ───────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")

    paper = {
        "id": "2603.06449",
        "title": "CaTok: Taming Mean Flows for One-Dimensional Causal Image Tokenization",
        "abstract": (
            "We present CaTok, a novel approach that leverages mean flow matching "
            "for causal 1D image tokenization. Unlike TiTok which uses bidirectional "
            "attention, CaTok enables autoregressive generation by design. We compare "
            "against VQGAN, TiTok, LlamaGen and achieve state-of-the-art FID on ImageNet."
        ),
    }
    refs = [
        {
            "title": "TiTok: An Image is Worth 32 Tokens",
            "abstract": "We propose TiTok, a compact 1D tokenizer for image reconstruction.",
        },
        {
            "title": "VQGAN: Taming Transformers for High-Resolution Image Synthesis",
            "abstract": "We use vector-quantized autoencoders and transformers for image generation.",
        },
        {
            "title": "LlamaGen: Autoregressive Image Generation",
            "abstract": "Scaling autoregressive models for class-conditional image generation.",
        },
        {
            "title": "Flow Matching for Generative Modeling",
            "abstract": "We propose flow matching, a simulation-free approach to generative modeling.",
        },
        {
            "title": "MaskGIT: Masked Generative Image Transformer",
            "abstract": "Bidirectional masked transformer for image generation.",
        },
    ]

    print("=== Scheme B test (will call LLM) ===")
    result = analyze_paper_scheme_b(paper, refs)
    print(json.dumps(result, indent=2, ensure_ascii=False))
