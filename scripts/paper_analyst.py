"""
paper_analyst.py — Production analyst for arxiv-radar v3.0.

Variant F prompt: single unified LLM call with LaTeX full-text + bib mapping.
  Input:  paper title + abstract + bib key→title mapping + LaTeX full text
  Output: cn_oneliner, cn_abstract, contribution_type, editorial_note,
          why_read, method_variants, core_cite

Model config:
  DEFAULT_MODEL  = "minimaxm25"  (fast, good quality)
  FALLBACK_MODEL = "gpt52"       (strong citation/lineage analysis, slower)

Prompt design decisions (validated 2026-03-12):
  - editorial_note: E2 three-section [前驱][贡献][判断] structured format
  - why_read:       E1 free critical style (specific who + what)
  - method_variants: E1 free style (base_method, variant_tag, description)
  - core_cite:      E2 structured (priority-ordered ≥10, must cover all contrasts+extends)

Uses stdlib only (urllib.request). No third-party dependencies.
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

DEFAULT_MODEL  = "minimaxm25"
FALLBACK_MODEL = "gpt52"

# ─────────────────── Production prompt (Variant F) ───────────────────

SYSTEM_PROMPT = """\
你是 CV 领域资深研究员，每周阅读 20+ 篇论文。分析给定论文，输出结构化 JSON。

分析要求：

cn_oneliner（≤45字）
格式：「基于[X]引入[Y]实现[Z]」或「把[A]和[B]结合解决[C]」
必须包含：具体的基础方法名 + 具体改动 + 具体效果。不要泛泛的"改进"。

cn_abstract（2-4句中文技术摘要）
完整。关键术语保留英文。必须完整，不得截断。

contribution_type（严格四选一）
- incremental: 在已有方法上做了有效但可预期的改进，"做了应该做的事"
- significant: 解决了领域内已知的难题，或提供了其他人可以复用的新方法/新框架
- story-heavy: 工程堆砌为主，叙事高于实质，"拿结果说话但说不清为什么 work"
- foundational: 改变了领域做事方式，未来方法会引用这篇作为起点
很多论文自称 novel 实为 incremental，从严判断。

editorial_note（必须按三段结构写，总字数 80-150 字）
[前驱] 这篇论文建立在哪些已有工作的基础上，核心模块各来自哪里。
[贡献] 去掉包装之后，作者真正做了什么新事情（用最简单的话）。
[判断] 这个贡献的实质价值：是真正解决了问题，还是有效但不深刻，或者夸大了困难/贡献。

why_read（1句，自由但要有判断力）
不要"如果你做这个领域可以看看"这种废话。说清楚谁值得读，具体会从中得到什么。

method_variants（方法变体列表）
- base_method: 具体已有方法名（小写，如 flextok, gigatok, nested-dropout）
- variant_tag: base_method:改动标签
- description: 改动一句话，说清楚原方法做什么、本文如何改造

core_cite（强制 ≥10 条，按重要性排序）
权重排序：
1. Method 章节直接构建在其上的工作（role=extends，最高权重）
2. 用到其组件/backbone/预训练模型（role=uses）
3. Experiments 中 baseline 对比（role=contrasts）
4. Introduction 中支持动机的引用（role=supports）
5. Related Work 背景引用（role=mentions）
不可省略：所有 contrasts 类 + 所有 extends 类引用。

role 选唯一最准确的值，五选一：extends | contrasts | uses | supports | mentions
禁止组合写法（不得输出"extends/uses"等），若有歧义选最主要的。

每条：title=原始英文标题（尽量从引用文献中提取完整标题）| role | note=具体关系

输出格式：严格 JSON，不要 markdown 代码块，不要任何额外文字：
{"cn_oneliner":"","cn_abstract":"","contribution_type":"","editorial_note":"","why_read":"","method_variants":[{"base_method":"","variant_tag":"","description":""}],"core_cite":[{"title":"","role":"","note":""}]}
"""


def _build_user_message(paper: dict, bib_mapping: dict, paper_text: str) -> str:
    """Build the user message with paper metadata + bib table + LaTeX text."""
    title    = paper.get("title", "")
    abstract = paper.get("abstract", "") or ""
    arxiv_id = paper.get("arxiv_id") or paper.get("id", "")

    bib_lines = []
    for key, info in list(bib_mapping.items())[:80]:
        arxiv = info.get("arxiv_id") or ""
        suffix = f" | arxiv:{arxiv}" if arxiv else ""
        bib_lines.append(f"  {key}: {info.get('title', '')}{suffix}")
    bib_table = "\n".join(bib_lines) if bib_lines else "(bib not available)"

    if len(paper_text) > 50000:
        paper_text = paper_text[:50000] + "\n... [truncated]"

    return (
        f"分析以下论文：\n\n"
        f"## 论文信息\n"
        f"- arxiv ID: {arxiv_id}\n"
        f"- 标题: {title}\n"
        f"- 摘要: {abstract}\n\n"
        f"## 引用映射表（bib key → 论文标题）\n"
        f"{bib_table}\n\n"
        f"## 论文正文（LaTeX）\n"
        f"{paper_text}\n\n"
        "只输出 JSON，不要其他文字。"
    )


def _format_ref_block(refs: list[dict], max_refs: int = 15) -> str:
    """Legacy: format top-N references for old-style prompts (kept for compat)."""
    lines = []
    for i, ref in enumerate(refs[:max_refs]):
        title   = ref.get("title", "Unknown")
        snippet = (ref.get("abstract", "") or "")[:150].strip()
        lines.append(f"[{i+1}] {title}" + (f"\n    {snippet}..." if snippet else ""))
    return "\n".join(lines) if lines else "(no references available)"


def _llm_call(messages: list[dict], model: str, timeout: int = 300) -> tuple[str, float]:
    """
    Call wanqing-proxy at localhost:4141.
    - Anthropic models (claude46, glm5, minimaxm25…) → POST /messages
    - OpenAI models (gpt52, deepseekv32…)            → POST /oai/chat/completions
    Returns (content_str, latency_s).
    """
    model_id = model.split("/")[-1] if "/" in model else model
    is_anthropic = model_id in ANTHROPIC_MODELS

    if is_anthropic:
        url = BASE_URL.rstrip("/") + "/messages"
        payload = json.dumps({
            "model": model_id,
            "messages": messages,
            "max_tokens": 8192,
        }).encode("utf-8")
    else:
        url = BASE_URL.rstrip("/") + "/oai/chat/completions"
        # system + user split for OAI
        oai_messages = messages
        if len(messages) == 1 and messages[0]["role"] == "user":
            oai_messages = messages  # already correct
        payload = json.dumps({
            "model": model_id,
            "messages": oai_messages,
            "temperature": 0.3,
            "max_tokens": 8192,
        }).encode("utf-8")

    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    latency = time.time() - t0

    data = json.loads(raw)
    if is_anthropic:
        blocks = data.get("content", [])
        text = next((b["text"] for b in blocks if b.get("type") == "text"), None)
        if text is None:
            raise ValueError(f"No text block in response: {blocks[:1]}")
        return text.strip(), latency
    return data["choices"][0]["message"]["content"].strip(), latency


def _parse_json_from_text(text: str) -> tuple[dict | None, str | None]:
    """Extract a JSON object from LLM output. Returns (dict, error_or_None)."""
    text = text.strip()
    # Strip markdown fences
    fenced = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    def _try_fixes(s: str) -> dict | None:
        # Pass 1: direct
        try: return json.loads(s)
        except json.JSONDecodeError: pass
        # Pass 2: replace curly quotes with straight
        s2 = s
        for bad, good in [('\u201c','"'),('\u201d','"'),('\u2018',"'"),('\u2019',"'"),('\u300a',''),('\u300b','')]:
            s2 = s2.replace(bad, good)
        try: return json.loads(s2)
        except json.JSONDecodeError: pass
        # Pass 3: remove unescaped inner quotes in JSON string values
        # Pattern: "...<quote>word<quote>..." → "...word..."
        s3 = re.sub(r'(?<=[\u4e00-\u9fff\w\s])"(?=[\u4e00-\u9fff\w\s])', '', s2)
        try: return json.loads(s3)
        except json.JSONDecodeError: pass
        # Pass 4: replace ALL inner embedded double quotes in string values
        # Find positions where a " is NOT at start/end of a JSON string
        def fix_inner_quotes(js: str) -> str:
            result = []
            in_str = False
            escape = False
            prev_structural = True  # whether last non-space char was structural
            for i, ch in enumerate(js):
                if escape:
                    result.append(ch)
                    escape = False
                elif ch == '\\':
                    result.append(ch)
                    escape = True
                elif ch == '"':
                    if not in_str:
                        in_str = True
                        result.append(ch)
                    else:
                        # Check if this ends the string (next non-space is , : } ])
                        rest = js[i+1:i+20].lstrip()
                        if rest and rest[0] in ',:}]':
                            in_str = False
                            result.append(ch)
                        else:
                            # Inner quote: drop it
                            result.append('')
                else:
                    result.append(ch)
            return ''.join(result)
        s4 = fix_inner_quotes(s2)
        try: return json.loads(s4)
        except json.JSONDecodeError: pass
        return None

    # Try the full text first
    parsed = _try_fixes(text)
    if parsed: return parsed, None

    # Extract JSON object
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        parsed = _try_fixes(text[start:end + 1])
        if parsed: return parsed, None
        return None, f"All parse strategies failed ({len(text)} chars)"
    return None, f"No JSON found ({len(text)} chars)"


def _verify_core_cite_titles(core_cite: list[dict], bib_mapping: dict) -> list[str]:
    """
    Check each core_cite title against the bib_mapping.
    Returns list of suspicious (likely hallucinated) titles.
    Uses word-set Jaccard similarity; threshold 0.35.
    """
    def normalize(s: str) -> set:
        s = s.lower()
        s = re.sub(r"[^a-z0-9\s]", " ", s)
        return set(w for w in s.split() if len(w) > 2)

    bib_title_sets = [normalize(info.get("title", "")) for info in bib_mapping.values()]
    suspicious = []
    for entry in core_cite:
        title = entry.get("title", "")
        if not title:
            continue
        t_words = normalize(title)
        if not t_words:
            continue
        max_sim = 0.0
        for bib_words in bib_title_sets:
            if not bib_words:
                continue
            inter = len(t_words & bib_words)
            union = len(t_words | bib_words)
            sim = inter / union if union else 0
            if sim > max_sim:
                max_sim = sim
        if max_sim < 0.35:
            suspicious.append(title)
    return suspicious


def analyze_paper(
    paper: dict,
    bib_mapping: dict,
    paper_text: str,
    model: str = DEFAULT_MODEL,
    fallback_model: str = FALLBACK_MODEL,
    retry_on_parse_fail: bool = True,
    system_prompt_override: str | None = None,
    hallucination_check: bool = False,   # disabled by default; enable for production
) -> dict:
    """
    Production paper analysis — Variant F prompt.

    Args:
        paper:        paper dict with 'title', 'abstract', 'arxiv_id' / 'id'
        bib_mapping:  {bib_key: {title, arxiv_id, venue}} from parsed .bib file
        paper_text:   LaTeX full text (concatenated sections, ≤50K chars used)
        model:        primary model (default: minimaxm25)
        fallback_model: used if primary parse fails (default: gpt52)
        retry_on_parse_fail: if True, retry with fallback_model on parse failure

    Returns:
        Dict with keys: cn_oneliner, cn_abstract, contribution_type, editorial_note,
        why_read, method_variants, core_cite, latency_s, model_used, parse_errors
    """
    output: dict[str, Any] = {
        "cn_oneliner":       "",
        "cn_abstract":       "",
        "contribution_type": "",
        "editorial_note":    "",
        "why_read":          "",
        "method_variants":   [],
        "core_cite":         [],
        "latency_s":         0.0,
        "model_used":        model,
        "parse_errors":      [],
    }

    def _attempt(m: str) -> tuple[dict | None, float, str | None]:
        _sp = system_prompt_override or SYSTEM_PROMPT
        is_anthropic = (m.split("/")[-1] if "/" in m else m) in ANTHROPIC_MODELS
        user_msg = _build_user_message(paper, bib_mapping, paper_text)
        if is_anthropic:
            messages = [{"role": "user", "content": f"[System]\n{_sp}\n\n[Task]\n{user_msg}"}]
        else:
            messages = [
                {"role": "system", "content": _sp},
                {"role": "user",   "content": user_msg},
            ]
        raw, lat = _llm_call(messages, model=m)
        parsed, err = _parse_json_from_text(raw)
        return parsed, lat, err

    # Primary attempt
    try:
        parsed, lat, err = _attempt(model)
        output["latency_s"] = round(lat, 2)
        if err:
            output["parse_errors"].append(f"{model}: {err}")
            logger.warning(f"[analyst] Parse error ({model}): {err}")
            parsed = None
    except Exception as e:
        output["parse_errors"].append(f"{model}: call_error: {e}")
        logger.error(f"[analyst] LLM error ({model}): {e}")
        parsed = None

    # Fallback attempt if primary failed
    if parsed is None and retry_on_parse_fail and fallback_model and fallback_model != model:
        logger.info(f"[analyst] Retrying with fallback {fallback_model}")
        try:
            parsed, lat, err = _attempt(fallback_model)
            output["latency_s"] += round(lat, 2)
            output["model_used"] = fallback_model
            if err:
                output["parse_errors"].append(f"{fallback_model}: {err}")
                logger.warning(f"[analyst] Fallback parse error ({fallback_model}): {err}")
                parsed = None
        except Exception as e:
            output["parse_errors"].append(f"{fallback_model}: call_error: {e}")
            logger.error(f"[analyst] Fallback LLM error ({fallback_model}): {e}")

    if parsed:
        EXPECTED = ["cn_oneliner","cn_abstract","contribution_type","editorial_note",
                    "why_read","method_variants","core_cite"]
        for key in EXPECTED:
            if key in parsed:
                output[key] = parsed[key]

    # ── core_cite title verification (opt-in) ────────────────────────
    if hallucination_check and output["core_cite"] and bib_mapping:
        suspicious = _verify_core_cite_titles(output["core_cite"], bib_mapping)
        if suspicious:
            logger.warning(f"[analyst] core_cite hallucination detected: {suspicious}")
            output["parse_errors"].append(f"hallucinated_titles: {suspicious}")
            # Build correction prompt and retry once
            bad_list = "\n".join(f"  - {t}" for t in suspicious)
            correction_model = output["model_used"]
            is_anth = (correction_model.split("/")[-1] if "/" in correction_model else correction_model) in ANTHROPIC_MODELS
            user_msg = _build_user_message(paper, bib_mapping, paper_text)
            warning = (
                f"⚠️ 上一次输出的 core_cite 包含以下无法在 bib 中验证的标题，"
                f"这些很可能是幻觉，请修正：\n{bad_list}\n\n"
                f"重要提示：core_cite 中每个 title 必须与上方 bib 映射表中的标题完全对应，"
                f"不得引用 bib 表之外的论文。请重新输出完整 JSON。"
            )
            _sp = system_prompt_override or SYSTEM_PROMPT
            if is_anth:
                corr_messages = [{"role": "user", "content": f"[System]\n{_sp}\n\n[Task]\n{user_msg}\n\n{warning}"}]
            else:
                corr_messages = [
                    {"role": "system", "content": _sp},
                    {"role": "user",   "content": f"{user_msg}\n\n{warning}"},
                ]
            try:
                corr_raw, corr_lat = _llm_call(corr_messages, model=correction_model)
                output["latency_s"] += round(corr_lat, 2)
                corr_parsed, corr_err = _parse_json_from_text(corr_raw)
                if corr_parsed and not corr_err:
                    corr_suspicious = _verify_core_cite_titles(
                        corr_parsed.get("core_cite", []), bib_mapping)
                    if len(corr_suspicious) < len(suspicious):
                        logger.info(f"[analyst] Correction reduced hallucinations {len(suspicious)}→{len(corr_suspicious)}")
                        for key in EXPECTED:
                            if key in corr_parsed:
                                output[key] = corr_parsed[key]
                        output["parse_errors"].append(f"correction_applied: {len(suspicious)}→{len(corr_suspicious)} hallucinations")
                    else:
                        logger.warning(f"[analyst] Correction didn't help, keeping original")
            except Exception as ce:
                logger.warning(f"[analyst] Correction call failed: {ce}")

    return output


# ── Legacy compat (used by older callers) ───────────────────────────

def analyze_paper_scheme_b(paper: dict, refs: list, model: str = "wq/claude46") -> dict:
    """Legacy wrapper. Prefer analyze_paper() for new code."""
    bib = {str(i): {"title": r.get("title",""), "arxiv_id": None} for i, r in enumerate(refs)}
    ref_text = _format_ref_block(refs)
    return analyze_paper(paper, bib, f"[References]\n{ref_text}", model=model, retry_on_parse_fail=False)


# ─────────────────── CLI test ───────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")

    arxiv_id = sys.argv[1] if len(sys.argv) > 1 else "2601.01535"
    model    = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_MODEL

    # Try to load from pre-downloaded source
    source_dir = Path(f"/tmp/pa_test/{arxiv_id}")
    paper_text = ""
    bib_mapping = {}

    if source_dir.exists():
        txt = source_dir / "paper_text.txt"
        if txt.exists():
            paper_text = txt.read_text()
            logger.info(f"Loaded paper_text: {len(paper_text)} chars")
        bib = source_dir / "bib_parsed.json"
        if bib.exists():
            bib_mapping = json.loads(bib.read_text())
            logger.info(f"Loaded bib: {len(bib_mapping)} entries")

    # Load paper metadata from DB
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from paper_db import PaperDB
        db = PaperDB()
        paper = db.get_paper(arxiv_id) or {"arxiv_id": arxiv_id, "title": arxiv_id, "abstract": ""}
    except Exception:
        paper = {"arxiv_id": arxiv_id, "title": arxiv_id, "abstract": ""}

    logger.info(f"Paper: {paper.get('title','?')[:60]}")
    logger.info(f"Model: {model}")

    result = analyze_paper(paper, bib_mapping, paper_text, model=model)
    print(json.dumps(result, indent=2, ensure_ascii=False))
