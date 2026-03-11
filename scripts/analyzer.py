"""
analyzer.py — Batch Chinese summary generator via sub-agent.

Spawns isolated sub-agent to generate:
  - 中文摘要 (Chinese abstract, 2-4 sentences)
  - 一句话通俗解释 (One-sentence plain explanation)

Input:  list of paper dicts (id, title, abstract)
Output: dict[paper_id] → {"cn_abstract": str, "cn_oneliner": str}
"""

from __future__ import annotations
import json
import logging
import subprocess
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BATCH_SIZE = 8  # Papers per sub-agent call


def _build_prompt(papers: list[dict]) -> str:
    """Build the prompt for a batch of papers."""
    paper_texts = []
    for p in papers:
        abstract = p.get("abstract", "")[:600]
        paper_texts.append(
            f'ID: {p["id"]}\n'
            f'Title: {p["title"]}\n'
            f'Abstract: {abstract}\n'
        )

    papers_block = "\n---\n".join(paper_texts)

    return f"""你是一名计算机视觉领域的论文摘要助手。请为以下每篇论文生成：
1. **cn_abstract**: 中文摘要（2-4句话，技术性描述，保留关键术语英文原文）
2. **cn_oneliner**: 一句话通俗解释（不超过40字，像发微博一样简明直白，核心贡献是什么）

严格返回 JSON 数组，每个元素格式：
{{"id": "论文ID", "cn_abstract": "...", "cn_oneliner": "..."}}

不要输出任何其他内容，只输出 JSON 数组。

以下是论文列表：

{papers_block}"""


def _call_agent(prompt: str) -> str | None:
    """Call OpenClaw's model via CLI for batch analysis."""
    try:
        # Use openclaw system event to trigger analysis
        # Since we're inside OpenClaw, we can use the same model
        result = subprocess.run(
            [
                "claude", "--permission-mode", "bypassPermissions",
                "--print", "--output-format", "text",
                "-p", prompt,
            ],
            capture_output=True, text=True, timeout=120,
            cwd="/tmp",
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        logger.warning(f"Agent call failed: rc={result.returncode} stderr={result.stderr[:200]}")
        return None
    except FileNotFoundError:
        logger.info("claude CLI not available, trying python fallback")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("Agent call timed out")
        return None
    except Exception as e:
        logger.warning(f"Agent call error: {e}")
        return None


def _call_openai_compatible(prompt: str) -> str | None:
    """Try OpenAI-compatible API for batch analysis."""
    import urllib.request

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None

    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("ARXIV_RADAR_LLM_MODEL", "gpt-4o-mini")

    try:
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 3000,
            "temperature": 0.3,
        }).encode()

        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"OpenAI API error: {e}")
        return None


def _parse_json_response(raw: str) -> list[dict]:
    """Parse JSON from LLM response, handling markdown code blocks."""
    # Strip markdown code fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last line (fences)
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # Try to find JSON array in text
    import re
    m = re.search(r'\[.*\]', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    logger.warning(f"Failed to parse JSON from response ({len(text)} chars)")
    return []


def _template_cn_summary(paper: dict) -> dict:
    """
    Fallback: generate template-based Chinese summary when LLM is unavailable.
    Not as good, but better than nothing.
    """
    title = paper.get("title", "")
    abstract = paper.get("abstract", "")[:300]

    # Extract first sentence
    first_sent = abstract.split(". ")[0].rstrip(".") + "." if abstract else ""

    return {
        "id": paper["id"],
        "cn_abstract": f"本文「{title}」。{first_sent}",
        "cn_oneliner": f"提出了{title[:30]}的新方法。",
    }


def analyze_papers(
    papers: list[dict],
    use_llm: bool = True,
) -> dict[str, dict]:
    """
    Generate Chinese summaries for a list of papers.

    Args:
        papers: List of paper dicts
        use_llm: Whether to try LLM (True) or use templates (False)

    Returns:
        dict mapping paper_id → {"cn_abstract": str, "cn_oneliner": str}
    """
    results: dict[str, dict] = {}

    if not use_llm:
        for p in papers:
            results[p["id"]] = _template_cn_summary(p)
        return results

    # Process in batches
    total = len(papers)
    for i in range(0, total, BATCH_SIZE):
        batch = papers[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
        logger.info(f"Analyzing batch {batch_num}/{total_batches} ({len(batch)} papers)...")

        prompt = _build_prompt(batch)

        # Try different backends
        raw = _call_agent(prompt)
        if not raw:
            raw = _call_openai_compatible(prompt)

        if raw:
            parsed = _parse_json_response(raw)
            for item in parsed:
                pid = item.get("id", "")
                if pid:
                    results[pid] = {
                        "cn_abstract": item.get("cn_abstract", ""),
                        "cn_oneliner": item.get("cn_oneliner", ""),
                    }

        # Fill in template fallbacks for any papers not covered
        for p in batch:
            if p["id"] not in results:
                results[p["id"]] = _template_cn_summary(p)

    logger.info(f"Analysis complete: {len(results)} papers processed")
    return results


def enrich_papers(papers: list[dict], analyses: dict[str, dict]) -> list[dict]:
    """Merge analysis results back into paper dicts (non-destructive)."""
    for paper in papers:
        analysis = analyses.get(paper["id"], {})
        # Only overwrite if analysis has actual content (preserve existing)
        if analysis.get("cn_abstract"):
            paper["cn_abstract"] = analysis["cn_abstract"]
        if analysis.get("cn_oneliner"):
            paper["cn_oneliner"] = analysis["cn_oneliner"]
    return papers


# ─────────────────────────── CLI Test ───────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")

    test_papers = [
        {
            "id": "2603.03276",
            "title": "Beyond Language Modeling: An Exploration of Multimodal Pretraining",
            "abstract": "The visual world offers a critical axis for advancing foundation models beyond language. We provide empirical clarity through controlled, from-scratch pretraining experiments. Our experiments yield four key insights: (i) RAE provides an optimal unified visual representation; (ii) visual and language data are complementary; (iii) unified multimodal training shows emergent capabilities.",
        },
        {
            "id": "2406.07550",
            "title": "An Image is Worth 32 Tokens for Reconstruction and Generation",
            "abstract": "We propose TiTok, a compact 1D tokenizer leveraging region redundancy to represent an image with only 32 tokens for image reconstruction and generation. TiTok achieves SOTA generation performance while being substantially faster.",
        },
    ]

    print("=== Analyzer Test ===")
    results = analyze_papers(test_papers, use_llm=True)
    for pid, analysis in results.items():
        print(f"\n[{pid}]")
        print(f"  中文摘要: {analysis['cn_abstract']}")
        print(f"  一句话: {analysis['cn_oneliner']}")
