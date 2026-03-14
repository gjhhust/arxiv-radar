"""
test_variants.py — 测试 3 种 agent prompt 变体 × 4 个模型

用法:
  python3 test_variants.py --run           # 执行所有未完成测试
  python3 test_variants.py --run --model claude46  # 只跑一个模型
  python3 test_variants.py --run --variant a       # 只跑一个变体
  python3 test_variants.py --status        # 查看测试状态
  python3 test_variants.py --report        # 生成汇总报告
  python3 test_variants.py --force         # 强制重跑所有
"""
from __future__ import annotations
import json
import logging
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
logger = logging.getLogger(__name__)

# ─── Config ───
SKILL_DIR = Path(__file__).resolve().parent.parent
TEST_DIR = SKILL_DIR / "test_agent_variants"
RESULTS_DIR = TEST_DIR / "results"
STATE_FILE = TEST_DIR / "test_state.json"
TEST_PAPER = "2601.01535"
SOURCE_DIR = Path("/tmp/pa_test") / TEST_PAPER

BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://localhost:4141")
API_KEY = os.environ.get("OPENAI_API_KEY", "test")
ANTHROPIC_MODELS = {"claude46","claude45","glm5","katcoder","kimik25","minimaxm21","minimaxm25","glm47"}

VARIANTS = ["a", "b", "c"]
MODELS = ["claude46", "glm5", "minimaxm25", "katcoder"]

# ─── Load paper content ───
def load_paper_content() -> dict:
    """Load pre-downloaded paper source files."""
    content = {}

    # Paper text (concatenated tex sections)
    paper_text_path = SOURCE_DIR / "paper_text.txt"
    if paper_text_path.exists():
        content["paper_text"] = paper_text_path.read_text()
    else:
        # Try to build from individual tex files
        tex_parts = []
        for sec in ["sec/1_intro.tex", "sec/2_related_work.tex", "sec/3_method.tex", "sec/4_experiment.tex"]:
            p = SOURCE_DIR / sec
            if p.exists():
                tex_parts.append(f"\n=== {sec} ===\n" + p.read_text())
        content["paper_text"] = "\n".join(tex_parts) if tex_parts else ""

    # Bib mapping
    bib_path = SOURCE_DIR / "bib_parsed.json"
    if bib_path.exists():
        content["bib_mapping"] = json.loads(bib_path.read_text())
    else:
        content["bib_mapping"] = {}

    # Paper metadata from DB
    sys.path.insert(0, str(SKILL_DIR / "scripts"))
    from paper_db import PaperDB
    db = PaperDB()
    paper = db.get_paper(TEST_PAPER)
    content["paper_meta"] = paper if paper else {}

    return content


def load_variant_prompt(variant: str) -> str:
    """Load variant system prompt from file."""
    path = TEST_DIR / f"variant_{variant}.md"
    return path.read_text()


def build_user_message(content: dict) -> str:
    """Build the user message with paper content."""
    meta = content["paper_meta"]
    title = meta.get("title", "")
    abstract = meta.get("abstract", "")

    # Build bib reference table
    bib = content.get("bib_mapping", {})
    bib_lines = []
    for key, info in bib.items():
        arxiv = info.get("arxiv_id") or ""
        arxiv_str = f" | arxiv:{arxiv}" if arxiv else ""
        bib_lines.append(f"  {key}: {info.get('title', '')}{arxiv_str}")
    bib_table = "\n".join(bib_lines[:80])  # Cap at 80 entries

    paper_text = content.get("paper_text", "")
    # Truncate to ~15K chars to keep within token limits
    if len(paper_text) > 50000:
        paper_text = paper_text[:50000] + "\n... [truncated]"

    return f"""分析以下论文：

## 论文信息
- arxiv ID: {TEST_PAPER}
- 标题: {title}
- 摘要: {abstract}

## 引用映射表（bib key → 论文标题）
{bib_table}

## 论文正文（LaTeX）
{paper_text}

请按照你的分析规范输出 JSON 结果。只输出 JSON，不要其他文字。"""


# ─── LLM Call ───
def llm_call(system: str, user: str, model: str, timeout: int = 300) -> tuple[str, float]:
    """Call wq proxy LLM."""
    model_id = model.split("/")[-1] if "/" in model else model
    is_anthropic = model_id in ANTHROPIC_MODELS

    messages = [
        {"role": "user", "content": f"[System Instructions]\n{system}\n\n[Task]\n{user}"}
    ]

    if is_anthropic:
        url = BASE_URL.rstrip("/") + "/messages"
        payload = json.dumps({
            "model": model_id,
            "messages": messages,
            "max_tokens": 8192,
        }).encode()
    else:
        url = BASE_URL.rstrip("/") + "/oai/chat/completions"
        payload = json.dumps({
            "model": model_id,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.3,
            "max_tokens": 4000,
        }).encode()

    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    })

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
    else:
        return data["choices"][0]["message"]["content"].strip(), latency


def parse_json(text: str) -> tuple[dict | None, str | None]:
    """Extract JSON from LLM output."""
    text = text.strip()
    # Strip markdown code fences
    fenced = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1]), None
        except json.JSONDecodeError as e:
            return None, f"JSONDecodeError: {e}"
    return None, f"No JSON found ({len(text)} chars)"


# ─── State Management ───
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"cells": {}, "started": datetime.now().isoformat()}

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


# ─── Run Tests ───
def run_cell(variant: str, model: str, content: dict, force: bool = False) -> dict:
    """Run one test cell (variant × model)."""
    cell_key = f"{variant}_{model}"
    state = load_state()

    if cell_key in state["cells"] and not force:
        logger.info(f"[SKIP] {cell_key} already done")
        return state["cells"][cell_key]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    system_prompt = load_variant_prompt(variant)
    user_msg = build_user_message(content)

    logger.info(f"[RUN] variant={variant} model={model}")
    logger.info(f"  system prompt: {len(system_prompt)} chars")
    logger.info(f"  user message: {len(user_msg)} chars")

    result = {
        "variant": variant,
        "model": model,
        "timestamp": datetime.now().isoformat(),
        "latency_s": 0,
        "parse_success": False,
        "error": None,
        "raw_output": "",
        "parsed": None,
    }

    try:
        raw, latency = llm_call(system_prompt, user_msg, model, timeout=300)
        result["latency_s"] = round(latency, 2)
        result["raw_output"] = raw

        parsed, err = parse_json(raw)
        if err:
            result["error"] = err
            logger.warning(f"  Parse error: {err}")
        else:
            result["parse_success"] = True
            result["parsed"] = parsed
            logger.info(f"  ✓ Parsed successfully, latency={latency:.1f}s")

            # Check field completeness
            expected = ["cn_oneliner", "cn_abstract", "contribution_type",
                       "editorial_note", "why_read", "method_variants", "core_cite"]
            filled = sum(1 for f in expected if parsed.get(f))
            result["fill_rate"] = round(filled / len(expected), 2)
            result["core_cite_count"] = len(parsed.get("core_cite", []))
            result["method_variant_count"] = len(parsed.get("method_variants", []))
            logger.info(f"  fill_rate={result['fill_rate']} core_cites={result['core_cite_count']}")

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"  ✗ Error: {e}")

    # Save individual result
    result_path = RESULTS_DIR / f"{cell_key}.json"
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))

    # Update state
    state["cells"][cell_key] = {
        "status": "done" if result["parse_success"] else "error",
        "latency_s": result["latency_s"],
        "parse_success": result["parse_success"],
        "fill_rate": result.get("fill_rate", 0),
        "error": result.get("error"),
        "timestamp": result["timestamp"],
    }
    save_state(state)

    return result


def run_all(force: bool = False, model_filter: str = None, variant_filter: str = None):
    """Run all test cells, one model at a time."""
    content = load_paper_content()
    logger.info(f"Paper: {content['paper_meta'].get('title', '?')[:60]}")
    logger.info(f"Paper text: {len(content.get('paper_text', ''))} chars")
    logger.info(f"Bib entries: {len(content.get('bib_mapping', {}))}")

    models = [model_filter] if model_filter else MODELS
    variants = [variant_filter] if variant_filter else VARIANTS

    total = len(models) * len(variants)
    done = 0

    for model in models:
        logger.info(f"\n{'='*60}")
        logger.info(f"MODEL: {model}")
        logger.info(f"{'='*60}")

        for variant in variants:
            done += 1
            logger.info(f"\n[{done}/{total}] variant={variant} model={model}")
            run_cell(variant, model, content, force=force)

            # Rate limit between calls
            if done < total:
                logger.info("  sleeping 3s...")
                time.sleep(3)

    logger.info(f"\n✓ All {total} cells complete")


def show_status():
    """Show test status."""
    state = load_state()
    cells = state.get("cells", {})

    print(f"\n{'Variant':>10} {'Model':>12} {'Status':>8} {'Latency':>8} {'Parse':>6} {'Fill':>5}")
    print("-" * 60)

    for model in MODELS:
        for variant in VARIANTS:
            key = f"{variant}_{model}"
            if key in cells:
                c = cells[key]
                status = "✓" if c["parse_success"] else "✗"
                print(f"{'Var-'+variant:>10} {model:>12} {status:>8} {c['latency_s']:>7.1f}s {c['parse_success']!s:>6} {c.get('fill_rate',0):>5.2f}")
            else:
                print(f"{'Var-'+variant:>10} {model:>12} {'pending':>8}")


def generate_report():
    """Generate summary report."""
    results = []
    for f in sorted(RESULTS_DIR.glob("*.json")):
        results.append(json.loads(f.read_text()))

    report_lines = [f"# Agent Variant Test Report — {TEST_PAPER}", f"Generated: {datetime.now().isoformat()}", ""]

    # Summary table
    report_lines.append("## Summary")
    report_lines.append(f"| Variant | Model | Latency | Parse | Fill Rate | Core Cites | Method Variants |")
    report_lines.append(f"|---------|-------|---------|-------|-----------|------------|-----------------|")

    for r in sorted(results, key=lambda x: (x["variant"], x["model"])):
        v = f"Var-{r['variant']}"
        m = r["model"]
        lat = f"{r['latency_s']:.1f}s"
        parse = "✓" if r["parse_success"] else "✗"
        fill = f"{r.get('fill_rate', 0):.0%}"
        cc = r.get("core_cite_count", "-")
        mv = r.get("method_variant_count", "-")
        report_lines.append(f"| {v} | {m} | {lat} | {parse} | {fill} | {cc} | {mv} |")

    # Detailed outputs
    report_lines.append("\n## Detailed Outputs\n")
    for r in sorted(results, key=lambda x: (x["variant"], x["model"])):
        report_lines.append(f"### Variant {r['variant'].upper()} × {r['model']}")
        if r["parsed"]:
            p = r["parsed"]
            report_lines.append(f"- **cn_oneliner**: {p.get('cn_oneliner', '-')}")
            report_lines.append(f"- **contribution_type**: {p.get('contribution_type', '-')}")
            report_lines.append(f"- **editorial_note**: {p.get('editorial_note', '-')}")
            report_lines.append(f"- **why_read**: {p.get('why_read', '-')}")
            report_lines.append(f"- **core_cite** ({len(p.get('core_cite', []))} items):")
            for c in p.get("core_cite", []):
                report_lines.append(f"  - [{c.get('role','')}] {c.get('title','')} — {c.get('note','')}")
            report_lines.append(f"- **method_variants** ({len(p.get('method_variants', []))} items):")
            for mv in p.get("method_variants", []):
                report_lines.append(f"  - {mv.get('variant_tag', '')}: {mv.get('description', '')}")
        else:
            report_lines.append(f"- **Error**: {r.get('error', 'unknown')}")
        report_lines.append("")

    report_path = TEST_DIR / "report.md"
    report_path.write_text("\n".join(report_lines))
    print(f"Report written to {report_path}")


# ─── CLI ───
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Test agent prompt variants")
    parser.add_argument("--run", action="store_true", help="Run tests")
    parser.add_argument("--status", action="store_true", help="Show status")
    parser.add_argument("--report", action="store_true", help="Generate report")
    parser.add_argument("--force", action="store_true", help="Force rerun")
    parser.add_argument("--model", type=str, help="Filter to one model")
    parser.add_argument("--variant", type=str, help="Filter to one variant (a/b/c)")
    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.report:
        generate_report()
    elif args.run:
        run_all(force=args.force, model_filter=args.model, variant_filter=args.variant)
    else:
        parser.print_help()
