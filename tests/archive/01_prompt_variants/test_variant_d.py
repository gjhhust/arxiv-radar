"""
test_variant_d.py — 测试融合方案 Variant D × 4 模型

用法:
  python3 test_variant_d.py --run                    # 全部跑
  python3 test_variant_d.py --run --model claude46    # 单模型
  python3 test_variant_d.py --status                  # 状态
  python3 test_variant_d.py --html                    # 生成交互式对比 HTML
"""
from __future__ import annotations
import json, logging, os, re, sys, time, urllib.request
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
logger = logging.getLogger(__name__)

SKILL_DIR = Path(__file__).resolve().parent.parent
TEST_DIR = SKILL_DIR / "test_agent_variants"
RESULTS_DIR = TEST_DIR / "results_d"
STATE_FILE = TEST_DIR / "test_state_d.json"
TEST_PAPER = "2601.01535"
SOURCE_DIR = Path("/tmp/pa_test") / TEST_PAPER

BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://localhost:4141")
API_KEY = os.environ.get("OPENAI_API_KEY", "test")
ANTHROPIC_MODELS = {"claude46","claude45","glm5","katcoder","kimik25","minimaxm21","minimaxm25","glm47"}

MODELS = ["minimaxm25", "claude46", "deepseekv32", "gpt52"]

def load_paper_content() -> dict:
    content = {}
    paper_text_path = SOURCE_DIR / "paper_text.txt"
    if paper_text_path.exists():
        content["paper_text"] = paper_text_path.read_text()
    else:
        tex_parts = []
        for sec in ["sec/1_intro.tex", "sec/2_related_work.tex", "sec/3_method.tex", "sec/4_experiment.tex"]:
            p = SOURCE_DIR / sec
            if p.exists():
                tex_parts.append(f"\n=== {sec} ===\n" + p.read_text())
        content["paper_text"] = "\n".join(tex_parts) if tex_parts else ""

    bib_path = SOURCE_DIR / "bib_parsed.json"
    if bib_path.exists():
        content["bib_mapping"] = json.loads(bib_path.read_text())
    else:
        content["bib_mapping"] = {}

    sys.path.insert(0, str(SKILL_DIR / "scripts"))
    from paper_db import PaperDB
    db = PaperDB()
    paper = db.get_paper(TEST_PAPER)
    content["paper_meta"] = paper if paper else {}
    return content


def build_user_message(content: dict) -> str:
    meta = content["paper_meta"]
    title = meta.get("title", "")
    abstract = meta.get("abstract", "")

    bib = content.get("bib_mapping", {})
    bib_lines = []
    for key, info in bib.items():
        arxiv = info.get("arxiv_id") or ""
        arxiv_str = f" | arxiv:{arxiv}" if arxiv else ""
        bib_lines.append(f"  {key}: {info.get('title', '')}{arxiv_str}")
    bib_table = "\n".join(bib_lines[:80])

    paper_text = content.get("paper_text", "")
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


def llm_call(system: str, user: str, model: str, timeout: int = 300) -> tuple[str, float]:
    model_id = model.split("/")[-1] if "/" in model else model
    is_anthropic = model_id in ANTHROPIC_MODELS

    if is_anthropic:
        url = BASE_URL.rstrip("/") + "/messages"
        payload = json.dumps({
            "model": model_id,
            "messages": [{"role": "user", "content": f"[System Instructions]\n{system}\n\n[Task]\n{user}"}],
            "max_tokens": 8192,
        }).encode()
    else:
        url = BASE_URL.rstrip("/") + "/oai/chat/completions"
        payload = json.dumps({
            "model": model_id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.3,
            "max_tokens": 8192,
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
            raise ValueError(f"No text block: {blocks[:1]}")
        return text.strip(), latency
    else:
        return data["choices"][0]["message"]["content"].strip(), latency


def parse_json(text: str) -> tuple[dict | None, str | None]:
    text = text.strip()
    # Strip markdown code fences
    fenced = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    # Clean Chinese quotes that might break JSON
    text = text.replace('\u201c', '"').replace('\u201d', '"')
    text = text.replace('\u2018', "'").replace('\u2019', "'")
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        pass
    # Find JSON object boundaries
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidate = text[start:end + 1]
        # Try direct parse
        try:
            return json.loads(candidate), None
        except json.JSONDecodeError:
            pass
        # Try cleaning more aggressively
        candidate2 = candidate.replace('\u201c', '').replace('\u201d', '')
        candidate2 = candidate2.replace('\u300a', '').replace('\u300b', '')
        try:
            return json.loads(candidate2), None
        except json.JSONDecodeError as e:
            return None, f"JSONDecodeError: {e}"
    return None, f"No JSON found ({len(text)} chars)"


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"cells": {}, "started": datetime.now().isoformat()}

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def run_cell(model: str, content: dict, force: bool = False) -> dict:
    state = load_state()
    if model in state["cells"] and not force:
        logger.info(f"[SKIP] {model} already done")
        return state["cells"][model]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    system_prompt = (TEST_DIR / "variant_d.md").read_text()
    user_msg = build_user_message(content)

    logger.info(f"[RUN] model={model}")

    result = {
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
            logger.info(f"  ✓ Parsed, latency={latency:.1f}s")

            expected = ["cn_oneliner", "cn_abstract", "contribution_type",
                       "editorial_note", "why_read", "method_variants", "core_cite"]
            filled = sum(1 for f in expected if parsed.get(f))
            result["fill_rate"] = round(filled / len(expected), 2)
            result["core_cite_count"] = len(parsed.get("core_cite", []))
            result["method_variant_count"] = len(parsed.get("method_variants", []))

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"  ✗ Error: {e}")

    (RESULTS_DIR / f"{model}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))

    state["cells"][model] = {
        "status": "done" if result["parse_success"] else "error",
        "latency_s": result["latency_s"],
        "parse_success": result["parse_success"],
        "fill_rate": result.get("fill_rate", 0),
        "error": result.get("error"),
    }
    save_state(state)
    return result


def run_all(force=False, model_filter=None):
    content = load_paper_content()
    logger.info(f"Paper: {content['paper_meta'].get('title', '?')[:60]}")
    models = [model_filter] if model_filter else MODELS
    for i, model in enumerate(models):
        logger.info(f"\n[{i+1}/{len(models)}] model={model}")
        run_cell(model, content, force=force)
        if i < len(models) - 1:
            logger.info("  sleeping 5s...")
            time.sleep(5)
    logger.info(f"\n✓ Done")


def show_status():
    state = load_state()
    print(f"\n{'Model':>14} {'Status':>8} {'Latency':>8} {'Parse':>6} {'Fill':>5}")
    print("-" * 50)
    for model in MODELS:
        if model in state.get("cells", {}):
            c = state["cells"][model]
            s = "✓" if c["parse_success"] else "✗"
            print(f"{model:>14} {s:>8} {c['latency_s']:>7.1f}s {c['parse_success']!s:>6} {c.get('fill_rate',0):>5.2f}")
        else:
            print(f"{model:>14} {'pending':>8}")


def generate_interactive_html():
    results = {}
    for f in sorted(RESULTS_DIR.glob("*.json")):
        model = f.stem
        data = json.loads(f.read_text())
        parsed = data.get("parsed") or {}
        results[model] = {
            "model": model,
            "latency": data.get("latency_s", 0),
            "data": parsed,
            "parse_ok": data.get("parse_success", False),
        }

    def esc(s):
        return str(s).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('"','&quot;')

    fields = ['cn_oneliner','cn_abstract','contribution_type','editorial_note','why_read','method_variants','core_cite']
    model_list = [m for m in MODELS if m in results]
    models_json = json.dumps(model_list)
    results_json = json.dumps({m: results[m]['data'] for m in model_list}, ensure_ascii=False)
    latency_json = json.dumps({m: results[m]['latency'] for m in model_list})

    html = f'''<!DOCTYPE html>
<html lang="zh"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Variant D Model Comparison — Interactive</title>
<style>
:root {{ --bg:#0f1117; --card:#1a1d27; --card2:#21253a; --border:#2e3250; --accent:#5b8dee; --green:#3ecf8e; --yellow:#f5a623; --red:#e05c5c; --purple:#a78bfa; --text:#e2e8f0; --muted:#8892a4; --mono:'JetBrains Mono',monospace; --orange:#f59e0b; }}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ background:var(--bg); color:var(--text); font-family:-apple-system,sans-serif; font-size:14px; line-height:1.7; }}
.container {{ max-width:1200px; margin:0 auto; padding:30px 20px; }}
h1 {{ font-size:20px; color:#fff; margin-bottom:4px; }}
h2 {{ font-size:16px; color:#fff; margin:28px 0 12px; border-left:3px solid var(--accent); padding-left:10px; }}
.meta {{ color:var(--muted); font-size:12px; margin-bottom:20px; }}

/* Tabs */
.tabs {{ display:flex; gap:6px; margin:14px 0; flex-wrap:wrap; }}
.tab {{ padding:8px 18px; border-radius:6px; cursor:pointer; font-size:13px; font-weight:600;
  background:var(--card); border:1px solid var(--border); color:var(--muted); transition:all 0.2s; }}
.tab:hover {{ border-color:var(--accent); color:var(--text); }}
.tab.active {{ background:var(--accent); border-color:var(--accent); color:#fff; }}
.tab .lat {{ font-size:10px; opacity:0.7; margin-left:4px; }}

/* Compare panels */
.compare-area {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin:14px 0; }}
.panel {{ background:var(--card); border:1px solid var(--border); border-radius:8px; padding:16px; min-height:100px; }}
.panel.selected {{ border-color:var(--green); }}
.panel h3 {{ font-size:13px; color:var(--accent); margin-bottom:10px; display:flex; justify-content:space-between; align-items:center; }}
.panel h3 .model-name {{ font-family:var(--mono); }}

/* Field sections */
.field-section {{ margin:20px 0; }}
.field-label {{ font-family:var(--mono); font-size:12px; color:var(--accent); font-weight:700; margin-bottom:6px; display:flex; align-items:center; gap:8px; }}
.field-content {{ font-size:13px; line-height:1.7; }}
.contrib {{ display:inline-block; padding:2px 8px; border-radius:8px; font-size:11px; font-weight:600; }}
.contrib.incremental {{ background:#1a2c5a; color:var(--accent); }}
.contrib.significant {{ background:#1f3a2a; color:var(--green); }}
.contrib.story-heavy {{ background:#3a341f; color:var(--yellow); }}
.contrib.foundational {{ background:#2a1a3a; color:var(--purple); }}

.cite-item {{ margin:4px 0; padding:5px 8px; background:#0a0d16; border-radius:4px; font-size:12px; }}
.cite-role {{ font-family:var(--mono); font-size:10px; padding:1px 5px; border-radius:3px; background:#21253a; color:var(--purple); margin-right:4px; }}
.cite-note {{ color:var(--muted); }}
.mv-item {{ margin:3px 0; padding:4px 8px; background:#0a0d16; border-radius:4px; font-size:12px; }}
.mv-tag {{ font-family:var(--mono); font-size:11px; color:var(--yellow); }}

/* Vote */
.vote-bar {{ display:flex; gap:8px; margin:20px 0; align-items:center; }}
.vote-btn {{ padding:8px 20px; border-radius:6px; cursor:pointer; font-size:13px; font-weight:600;
  border:2px solid var(--border); background:var(--card); color:var(--text); transition:all 0.2s; }}
.vote-btn:hover {{ border-color:var(--accent); }}
.vote-btn.voted {{ border-color:var(--green); background:#1a2c1a; color:var(--green); }}
.vote-summary {{ margin:10px 0; padding:12px 16px; background:var(--card); border:1px solid var(--border); border-radius:6px; font-size:13px; }}
.vote-summary .count {{ font-family:var(--mono); color:var(--green); font-weight:700; }}

/* Toggle */
.toggle-row {{ display:flex; gap:10px; margin:10px 0; }}
.toggle-btn {{ padding:4px 12px; border-radius:4px; cursor:pointer; font-size:12px;
  background:var(--card2); border:1px solid var(--border); color:var(--muted); }}
.toggle-btn.active {{ background:var(--accent); border-color:var(--accent); color:#fff; }}
</style>
</head><body>
<div class="container">
<h1>Variant D 模型对比 — 交互式评测</h1>
<div class="meta">论文: 2601.01535 — Improving Flexible Image Tokenizers for Autoregressive Image Generation<br>
融合方案 Variant D · 点击模型 tab 查看输出 · 为每个字段投票选出最佳模型</div>

<h2>选择对比模型</h2>
<div class="tabs" id="modelTabs"></div>

<h2>逐字段对比</h2>
<div id="compareArea"></div>

<h2>投票汇总</h2>
<div class="vote-summary" id="voteSummary">点击每个字段下方的模型按钮进行投票</div>

</div>

<script>
const MODELS = {models_json};
const RESULTS = {results_json};
const LATENCY = {latency_json};
const FIELDS = {json.dumps(fields)};
const FIELD_LABELS = {{
  cn_oneliner: "cn_oneliner（≤45字方法核心）",
  cn_abstract: "cn_abstract（中文技术摘要）",
  contribution_type: "contribution_type（贡献类型）",
  editorial_note: "editorial_note（编辑深度判断）",
  why_read: "why_read（推荐理由）",
  method_variants: "method_variants（方法变体）",
  core_cite: "core_cite（核心引用）"
}};

let selectedModels = MODELS.slice(0, 2);
let votes = {{}};
FIELDS.forEach(f => votes[f] = null);

function esc(s) {{ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }}

function renderTabs() {{
  const container = document.getElementById('modelTabs');
  container.innerHTML = MODELS.map(m => {{
    const active = selectedModels.includes(m) ? ' active' : '';
    const lat = LATENCY[m] ? ` (${{LATENCY[m].toFixed(1)}}s)` : '';
    return `<div class="tab${{active}}" onclick="toggleModel('${{m}}')">${{m}}<span class="lat">${{lat}}</span></div>`;
  }}).join('');
}}

function toggleModel(m) {{
  const idx = selectedModels.indexOf(m);
  if (idx >= 0) {{
    if (selectedModels.length > 1) selectedModels.splice(idx, 1);
  }} else {{
    if (selectedModels.length >= 2) selectedModels.shift();
    selectedModels.push(m);
  }}
  renderTabs();
  renderCompare();
}}

function renderFieldContent(model, field) {{
  const data = RESULTS[model] || {{}};
  const val = data[field];
  if (!val) return '<span style="color:var(--red)">—</span>';

  if (field === 'contribution_type') {{
    return `<span class="contrib ${{val}}">${{val}}</span>`;
  }}
  if (field === 'method_variants' && Array.isArray(val)) {{
    return val.map(mv =>
      `<div class="mv-item"><span class="mv-tag">${{esc(mv.variant_tag||'')}}</span> — ${{esc(mv.description||'')}}</div>`
    ).join('');
  }}
  if (field === 'core_cite' && Array.isArray(val)) {{
    return val.map(c =>
      `<div class="cite-item"><span class="cite-role">${{c.role||'?'}}</span> <strong>${{esc((c.title||'').substring(0,70))}}</strong><br><span class="cite-note">${{esc(c.note||'')}}</span></div>`
    ).join('');
  }}
  return esc(String(val));
}}

function renderCompare() {{
  const area = document.getElementById('compareArea');
  let html = '';

  FIELDS.forEach(field => {{
    html += `<div class="field-section">`;
    html += `<div class="field-label">${{FIELD_LABELS[field] || field}}</div>`;
    html += `<div class="compare-area">`;

    selectedModels.forEach(m => {{
      const voted = votes[field] === m;
      html += `<div class="panel${{voted ? ' selected' : ''}}">`;
      html += `<h3><span class="model-name">${{m}}</span><span style="font-size:11px;color:var(--muted)">${{LATENCY[m]?.toFixed(1)}}s</span></h3>`;
      html += `<div class="field-content">${{renderFieldContent(m, field)}}</div>`;
      html += `</div>`;
    }});

    html += `</div>`;

    // Vote buttons for ALL models
    html += `<div class="vote-bar">`;
    html += `<span style="font-size:12px;color:var(--muted);margin-right:8px;">👆 选最佳:</span>`;
    MODELS.forEach(m => {{
      const voted = votes[field] === m;
      html += `<div class="vote-btn${{voted ? ' voted' : ''}}" onclick="vote('${{field}}','${{m}}')">${{m}}</div>`;
    }});
    html += `</div>`;
    html += `</div>`;
  }});

  area.innerHTML = html;
}}

function vote(field, model) {{
  votes[field] = votes[field] === model ? null : model;
  renderCompare();
  renderVoteSummary();
}}

function renderVoteSummary() {{
  const summary = document.getElementById('voteSummary');
  const counts = {{}};
  MODELS.forEach(m => counts[m] = 0);
  let voted = 0;
  Object.values(votes).forEach(v => {{ if (v) {{ counts[v]++; voted++; }} }});

  if (voted === 0) {{
    summary.innerHTML = '点击每个字段下方的模型按钮进行投票';
    return;
  }}

  const sorted = Object.entries(counts).sort((a,b) => b[1]-a[1]);
  let html = `<strong>投票结果 (${{voted}}/${{FIELDS.length}} 字段已投):</strong><br><br>`;
  sorted.forEach(([m, c]) => {{
    const bar = '█'.repeat(c) + '░'.repeat(FIELDS.length - c);
    html += `<span class="count">${{m}}</span>: ${{bar}} (${{c}}/${{FIELDS.length}})<br>`;
  }});

  // Detail
  html += '<br><strong>逐字段:</strong><br>';
  FIELDS.forEach(f => {{
    const v = votes[f];
    html += `${{FIELD_LABELS[f]?.split('（')[0] || f}}: ${{v ? `<span class="count">${{v}}</span>` : '<span style="color:var(--muted)">未投</span>'}}<br>`;
  }});

  summary.innerHTML = html;
}}

// Init
renderTabs();
renderCompare();
</script>
</body></html>'''

    out = TEST_DIR / "comparison_interactive.html"
    out.write_text(html)
    print(f"✓ Interactive HTML: {out} ({len(html)} bytes)")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--html", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--model", type=str)
    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.html:
        generate_interactive_html()
    elif args.run:
        run_all(force=args.force, model_filter=args.model)
    else:
        parser.print_help()
