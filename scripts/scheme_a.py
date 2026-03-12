"""
scheme_a.py — Scheme A "Specialist Pipeline" for arxiv-radar v3.0 A/B test.

Two-call LLM pipeline:
  Call 1 (Chinese Summary): paper title + abstract → cn_abstract, cn_oneliner
  Call 2 (Analysis):        paper + abstract + top-10 ref titles →
                               contribution_type, editorial_note, why_read,
                               method_variants, key_refs

Uses urllib.request (stdlib only, no requests dependency).
All LLM calls go to an OpenAI-compatible API endpoint.
JSON parse failures are recorded in parse_errors and do NOT raise.
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

BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://localhost:4141/v1")
API_KEY  = os.environ.get("OPENAI_API_KEY", "test")

# ─────────────────── Prompts ───────────────────

SUMMARY_PROMPT_TEMPLATE = """\
你是一名计算机视觉领域的论文摘要助手。请为以下论文生成：

1. **cn_abstract**: 中文技术摘要（2-4句话，保留关键术语的英文原文）
2. **cn_oneliner**: 一句话通俗说明（≤40字，像发微博一样简明，核心贡献是什么）

严格返回 JSON 对象，格式：
{{"cn_abstract": "...", "cn_oneliner": "..."}}

不要输出任何其他内容，只输出 JSON 对象。

论文标题: {title}
摘要: {abstract}
"""

ANALYSIS_PROMPT_TEMPLATE = """\
你是一名CV论文评审专家。请分析以下论文，综合判断其贡献类型、编辑价值和方法谱系。

## 论文信息
标题: {title}
摘要: {abstract}

## 该论文引用的Top 10参考文献（标题）
{ref_titles}

## 输出要求
严格返回 JSON 对象，包含以下字段：

{{
  "contribution_type": "<incremental|significant|story-heavy|foundational>",
  "editorial_note": "<1-2句编辑判断，评价方法创新性和跨域价值>",
  "why_read": "<1句推荐理由，说明值不值得读、为什么>",
  "method_variants": [
    {{"base_method": "<基础方法名，小写>", "variant_tag": "<base:variant-approach>", "description": "<一句话说明怎么改的>"}}
  ],
  "key_refs": [
    {{"title": "<引用论文标题>", "stance": "<extends|contrasts|uses|supports|mentions>", "note": "<一句话说明关系>"}}
  ]
}}

contribution_type 说明：
- incremental: 在已有方法上小幅改进
- significant: 有实质性方法创新或跨任务推广
- story-heavy: 工程为主，叙事过度，方法贡献有限
- foundational: 开创性工作，影响深远

只输出 JSON，不要其他文字。
"""


# ─────────────────── LLM call helper ───────────────────

def _llm_call(messages: list[dict], model: str, timeout: int = 60) -> tuple[str, float]:
    """
    POST to BASE_URL/chat/completions and return (content, latency_s).

    Raises urllib.error.URLError / json.JSONDecodeError on failure.
    """
    url = BASE_URL.rstrip("/") + "/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 2048,
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
    content = data["choices"][0]["message"]["content"].strip()
    return content, latency


def _parse_json_from_text(text: str) -> tuple[dict | None, str | None]:
    """
    Extract a JSON object from LLM output text.

    Returns (parsed_dict, error_msg). error_msg is None on success.
    """
    # Strip markdown code fences
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    # Try direct parse
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        pass

    # Find first {...} block
    start = text.find("{")
    end   = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1]), None
        except json.JSONDecodeError as e:
            return None, f"JSONDecodeError: {e}"

    return None, f"No JSON object found in response ({len(text)} chars)"


# ─────────────────── Main function ───────────────────

def analyze_paper_scheme_a(paper: dict, refs: list, model: str = "wq/claude46") -> dict:
    """
    Two-call specialist pipeline for a single paper.

    Call 1 — Chinese Summary:
        Input:  paper title + abstract
        Output: cn_abstract, cn_oneliner

    Call 2 — Editorial Analysis:
        Input:  paper title + abstract + top-10 ref titles
        Output: contribution_type, editorial_note, why_read,
                method_variants, key_refs

    Args:
        paper: paper dict with at least 'title' and 'abstract'
        refs:  list of reference paper dicts (used for top-10 titles)
        model: LLM model alias (e.g. "wq/claude46")

    Returns:
        Merged output dict containing all produced fields plus:
          latency_s   — total wall-clock seconds for both calls
          parse_errors — list of parse error strings (empty = all OK)
    """
    title    = paper.get("title", "")
    abstract = paper.get("abstract", "")[:800]
    parse_errors: list[str] = []
    total_latency = 0.0

    # ── Call 1: Chinese Summary ────────────────────────────────────────────
    summary_prompt = SUMMARY_PROMPT_TEMPLATE.format(title=title, abstract=abstract)
    cn_abstract = ""
    cn_oneliner = ""

    try:
        raw1, lat1 = _llm_call(
            [{"role": "user", "content": summary_prompt}],
            model=model,
        )
        total_latency += lat1
        parsed1, err1 = _parse_json_from_text(raw1)
        if err1:
            parse_errors.append(f"call1_parse: {err1}")
            logger.warning(f"[scheme_a] Call 1 parse error for {paper.get('id', '?')}: {err1}")
        else:
            cn_abstract = parsed1.get("cn_abstract", "")
            cn_oneliner = parsed1.get("cn_oneliner", "")
    except Exception as e:
        parse_errors.append(f"call1_error: {e}")
        logger.error(f"[scheme_a] Call 1 LLM error for {paper.get('id', '?')}: {e}")

    # ── Call 2: Editorial Analysis ─────────────────────────────────────────
    ref_titles = "\n".join(
        f"{i+1}. {r.get('title', '')}"
        for i, r in enumerate(refs[:10])
    ) or "(no references available)"

    analysis_prompt = ANALYSIS_PROMPT_TEMPLATE.format(
        title=title,
        abstract=abstract,
        ref_titles=ref_titles,
    )
    contribution_type = ""
    editorial_note    = ""
    why_read          = ""
    method_variants: list[dict] = []
    key_refs: list[dict]        = []

    try:
        raw2, lat2 = _llm_call(
            [{"role": "user", "content": analysis_prompt}],
            model=model,
        )
        total_latency += lat2
        parsed2, err2 = _parse_json_from_text(raw2)
        if err2:
            parse_errors.append(f"call2_parse: {err2}")
            logger.warning(f"[scheme_a] Call 2 parse error for {paper.get('id', '?')}: {err2}")
        else:
            contribution_type = parsed2.get("contribution_type", "")
            editorial_note    = parsed2.get("editorial_note", "")
            why_read          = parsed2.get("why_read", "")
            method_variants   = parsed2.get("method_variants", [])
            key_refs          = parsed2.get("key_refs", [])
    except Exception as e:
        parse_errors.append(f"call2_error: {e}")
        logger.error(f"[scheme_a] Call 2 LLM error for {paper.get('id', '?')}: {e}")

    return {
        # Call 1 output
        "cn_abstract":       cn_abstract,
        "cn_oneliner":       cn_oneliner,
        # Call 2 output
        "contribution_type": contribution_type,
        "editorial_note":    editorial_note,
        "why_read":          why_read,
        "method_variants":   method_variants,
        "key_refs":          key_refs,
        # Metadata
        "latency_s":   round(total_latency, 3),
        "parse_errors": parse_errors,
    }


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
        {"title": "TiTok: An Image is Worth 32 Tokens"},
        {"title": "VQGAN: Taming Transformers for High-Resolution Image Synthesis"},
        {"title": "LlamaGen: Autoregressive Image Generation"},
        {"title": "Flow Matching for Generative Modeling"},
        {"title": "MaskGIT: Masked Generative Image Transformer"},
    ]

    print("=== Scheme A test (will call LLM) ===")
    result = analyze_paper_scheme_a(paper, refs)
    print(json.dumps(result, indent=2, ensure_ascii=False))
