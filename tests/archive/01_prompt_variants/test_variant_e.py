"""
test_variant_e.py — E1 vs E2 × claude46 / gpt52 / minimaxm25

用法:
  python3 test_variant_e.py --run
  python3 test_variant_e.py --run --model claude46
  python3 test_variant_e.py --run --variant e1
  python3 test_variant_e.py --status
  python3 test_variant_e.py --html
"""
from __future__ import annotations
import json, logging, os, re, sys, time, urllib.request
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
logger = logging.getLogger(__name__)

SKILL_DIR = Path(__file__).resolve().parent.parent
TEST_DIR = SKILL_DIR / "test_agent_variants"
RESULTS_DIR = TEST_DIR / "results_e"
STATE_FILE = TEST_DIR / "test_state_e.json"
TEST_PAPER = "2601.01535"
SOURCE_DIR = Path("/tmp/pa_test") / TEST_PAPER

BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://localhost:4141")
API_KEY = os.environ.get("OPENAI_API_KEY", "test")
ANTHROPIC_MODELS = {"claude46","claude45","glm5","katcoder","kimik25","minimaxm21","minimaxm25","glm47"}

VARIANTS = ["e1", "e2"]
MODELS = ["claude46", "gpt52", "minimaxm25"]

VARIANT_LABELS = {
    "e1": "E1: Free Critical（自由批判型）",
    "e2": "E2: Structured Critical（结构化批判型）",
}
MODEL_NOTES = {
    "claude46": "参照 · 质量基准",
    "gpt52": "血缘分析强",
    "minimaxm25": "速度快",
}

# ── Paper content ──────────────────────────────────────────
def load_paper_content() -> dict:
    content = {}
    p = SOURCE_DIR / "paper_text.txt"
    if p.exists():
        content["paper_text"] = p.read_text()
    else:
        parts = []
        for sec in ["sec/1_intro.tex","sec/2_related_work.tex","sec/3_method.tex","sec/4_experiment.tex"]:
            sp = SOURCE_DIR / sec
            if sp.exists():
                parts.append(f"\n=== {sec} ===\n" + sp.read_text())
        content["paper_text"] = "\n".join(parts)

    bib = SOURCE_DIR / "bib_parsed.json"
    content["bib_mapping"] = json.loads(bib.read_text()) if bib.exists() else {}

    sys.path.insert(0, str(SKILL_DIR / "scripts"))
    from paper_db import PaperDB
    db = PaperDB()
    content["paper_meta"] = db.get_paper(TEST_PAPER) or {}
    return content


def build_user_message(content: dict) -> str:
    meta = content["paper_meta"]
    bib = content.get("bib_mapping", {})
    bib_lines = [
        f"  {k}: {v.get('title','')}{ ' | arxiv:' + v['arxiv_id'] if v.get('arxiv_id') else ''}"
        for k, v in list(bib.items())[:80]
    ]
    paper_text = content.get("paper_text", "")[:50000]

    return (
        f"分析以下论文：\n\n"
        f"## 论文信息\n"
        f"- arxiv ID: {TEST_PAPER}\n"
        f"- 标题: {meta.get('title','')}\n"
        f"- 摘要: {meta.get('abstract','')}\n\n"
        f"## 引用映射表（bib key → 论文标题）\n"
        + "\n".join(bib_lines)
        + f"\n\n## 论文正文（LaTeX）\n{paper_text}\n\n"
        "只输出 JSON，不要其他文字。"
    )


# ── LLM ────────────────────────────────────────────────────
def llm_call(system: str, user: str, model: str, timeout: int = 360) -> tuple[str, float]:
    is_anthropic = model in ANTHROPIC_MODELS
    if is_anthropic:
        url = BASE_URL.rstrip("/") + "/messages"
        body = {"model": model,
                "messages": [{"role":"user","content":f"[System]\n{system}\n\n[Task]\n{user}"}],
                "max_tokens": 8192}
    else:
        url = BASE_URL.rstrip("/") + "/oai/chat/completions"
        body = {"model": model,
                "messages": [{"role":"system","content":system},{"role":"user","content":user}],
                "temperature": 0.3, "max_tokens": 8192}

    req = urllib.request.Request(url,
        data=json.dumps(body).encode(),
        headers={"Content-Type":"application/json","Authorization":f"Bearer {API_KEY}"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    latency = time.time() - t0
    data = json.loads(raw)
    if is_anthropic:
        text = next((b["text"] for b in data.get("content",[]) if b.get("type")=="text"), None)
        if text is None: raise ValueError(f"No text block: {data}")
        return text.strip(), latency
    return data["choices"][0]["message"]["content"].strip(), latency


def parse_json(text: str) -> tuple[dict | None, str | None]:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if m: text = m.group(1).strip()
    # clean curly quotes
    for bad, good in [('\u201c','"'),('\u201d','"'),('\u2018',"'"),('\u2019',"'")]:
        text = text.replace(bad, good)
    try: return json.loads(text), None
    except: pass
    s, e = text.find("{"), text.rfind("}")
    if s >= 0 and e > s:
        cand = text[s:e+1]
        try: return json.loads(cand), None
        except:
            cand2 = cand.replace('\u201c','').replace('\u201d','').replace('\u300a','').replace('\u300b','')
            try: return json.loads(cand2), None
            except json.JSONDecodeError as ex: return None, str(ex)
    return None, f"No JSON ({len(text)} chars)"


# ── State ──────────────────────────────────────────────────
def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"cells":{}}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


# ── Run ────────────────────────────────────────────────────
def run_cell(variant: str, model: str, content: dict, force=False) -> dict:
    key = f"{variant}_{model}"
    state = load_state()
    if key in state["cells"] and not force:
        logger.info(f"[SKIP] {key}")
        return state["cells"][key]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    system = (TEST_DIR / f"variant_{variant}.md").read_text()
    user = build_user_message(content)
    logger.info(f"[RUN] {key}  prompt={len(system)}c  input={len(user)}c")

    result = {"variant":variant,"model":model,"timestamp":datetime.now().isoformat(),
              "latency_s":0,"parse_success":False,"error":None,"raw_output":"","parsed":None}
    try:
        raw, lat = llm_call(system, user, model)
        result["latency_s"] = round(lat, 2)
        result["raw_output"] = raw
        parsed, err = parse_json(raw)
        if err:
            result["error"] = err
            logger.warning(f"  parse error: {err[:80]}")
        else:
            result.update(parse_success=True, parsed=parsed)
            cc = len(parsed.get("core_cite",[]))
            mv = len(parsed.get("method_variants",[]))
            result.update(fill_rate=1.0, core_cite_count=cc, method_variant_count=mv)
            logger.info(f"  ✓ {lat:.1f}s  core_cites={cc}  mv={mv}")
    except Exception as ex:
        result["error"] = str(ex)
        logger.error(f"  ✗ {ex}")

    (RESULTS_DIR / f"{key}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    state = load_state()
    state["cells"][key] = {"status":"done" if result["parse_success"] else "error",
                           "latency_s":result["latency_s"],
                           "parse_success":result["parse_success"],
                           "core_cite_count":result.get("core_cite_count",0),
                           "error":result.get("error")}
    save_state(state)
    return result


def run_all(force=False, model_filter=None, variant_filter=None):
    content = load_paper_content()
    logger.info(f"Paper: {content['paper_meta'].get('title','?')[:60]}")
    models  = [model_filter]   if model_filter   else MODELS
    variants = [variant_filter] if variant_filter  else VARIANTS
    pairs = [(v,m) for m in models for v in variants]   # group by model
    for i,(v,m) in enumerate(pairs):
        logger.info(f"\n[{i+1}/{len(pairs)}] {v} × {m}")
        run_cell(v, m, content, force)
        if i < len(pairs)-1: time.sleep(4)
    logger.info("\n✓ All done")


# ── Status ─────────────────────────────────────────────────
def show_status():
    state = load_state()
    print(f"\n{'Variant':>6} {'Model':>12} {'Status':>6} {'Latency':>8} {'cites':>6}")
    print("-"*45)
    for m in MODELS:
        for v in VARIANTS:
            k = f"{v}_{m}"
            c = state.get("cells",{}).get(k)
            if c:
                s = "✓" if c["parse_success"] else "✗"
                print(f"{v:>6} {m:>12} {s:>6} {c['latency_s']:>7.1f}s {c.get('core_cite_count',0):>6}")
            else:
                print(f"{v:>6} {m:>12} {'…':>6}")


# ── HTML ───────────────────────────────────────────────────
def generate_html():
    # Load all results
    all_results = {}
    for f in RESULTS_DIR.glob("*.json"):
        d = json.loads(f.read_text())
        if not d.get("parsed") and d.get("raw_output"):
            raw = d["raw_output"]
            for bad,good in [('\u201c','"'),('\u201d','"')]:
                raw = raw.replace(bad,good)
            s,e = raw.find("{"), raw.rfind("}")
            if s>=0 and e>s:
                try: d["parsed"] = json.loads(raw[s:e+1])
                except: pass
        all_results[f.stem] = d

    def esc(s): return str(s).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('"','&quot;')

    fields = ['cn_oneliner','cn_abstract','contribution_type','editorial_note','why_read','method_variants','core_cite']
    field_labels = {
        'cn_oneliner': '📌 cn_oneliner', 'cn_abstract': '📄 cn_abstract',
        'contribution_type': '🏷 contribution_type', 'editorial_note': '✍️ editorial_note',
        'why_read': '👁 why_read', 'method_variants': '🔧 method_variants',
        'core_cite': '📚 core_cite'
    }

    cells_data = {}
    for m in MODELS:
        for v in VARIANTS:
            k = f"{v}_{m}"
            d = all_results.get(k, {})
            cells_data[k] = {
                "model": m, "variant": v,
                "latency": d.get("latency_s", 0),
                "data": d.get("parsed") or {},
            }

    cells_json   = json.dumps(cells_data, ensure_ascii=False)
    models_json  = json.dumps(MODELS)
    variants_json= json.dumps(VARIANTS)
    fields_json  = json.dumps(fields)
    labels_json  = json.dumps(field_labels, ensure_ascii=False)
    model_notes  = json.dumps(MODEL_NOTES, ensure_ascii=False)
    variant_labels = json.dumps(VARIANT_LABELS, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Variant E — Editorial & CoreCite 批判优化测试</title>
<style>
:root {{
  --bg:#0d0f18; --card:#141720; --card2:#1a1d2b; --border:#252840;
  --accent:#6c8ef5; --green:#3ecf8e; --yellow:#f5a623; --red:#e05c5c;
  --purple:#b08ef5; --orange:#f59e0b; --cyan:#22d3ee;
  --text:#dde3f0; --muted:#7a849a; --mono:'JetBrains Mono',monospace;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);font-family:-apple-system,sans-serif;font-size:13px;line-height:1.7}}
.wrap{{max-width:1400px;margin:0 auto;padding:24px 16px}}
h1{{font-size:18px;color:#fff;margin-bottom:3px}}
.meta{{color:var(--muted);font-size:11px;margin-bottom:18px}}

/* ── TOP CONTROLS ── */
.controls{{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:16px}}
.section-title{{font-size:14px;font-weight:700;color:#fff;padding:4px 0;border-bottom:1px solid var(--border);margin:22px 0 10px}}

/* ── MODEL / VARIANT TOGGLES ── */
.toggle-group{{display:flex;gap:6px;flex-wrap:wrap}}
.toggle-label{{font-size:11px;color:var(--muted);margin-right:4px;align-self:center}}
.tbtn{{padding:6px 14px;border-radius:20px;cursor:pointer;font-size:12px;font-weight:600;
  background:var(--card2);border:1px solid var(--border);color:var(--muted);transition:.15s}}
.tbtn:hover{{border-color:var(--accent);color:var(--text)}}
.tbtn.on{{background:var(--accent);border-color:var(--accent);color:#fff}}
.tbtn.ref{{border-color:#555;color:#aaa}} /* claude46 reference style */

/* ── GRID ── */
.grid-header{{display:grid;gap:8px;margin-bottom:6px}}
.col-header{{background:var(--card2);border:1px solid var(--border);border-radius:6px;
  padding:8px 12px;text-align:center}}
.col-header .model{{font-weight:700;font-size:13px}}
.col-header .vnote{{font-size:10px;color:var(--muted)}}
.col-header .lat{{font-size:10px;color:var(--cyan);font-family:var(--mono)}}

/* ── FIELD ROWS ── */
.field-row{{margin:14px 0}}
.field-row-header{{font-size:12px;font-weight:700;color:var(--accent);
  font-family:var(--mono);padding:5px 0;border-top:1px solid var(--border);
  display:flex;align-items:center;justify-content:space-between}}
.row-cells{{display:grid;gap:8px}}
.cell{{background:var(--card);border:1px solid var(--border);border-radius:6px;
  padding:10px 12px;font-size:12px;line-height:1.7;min-height:50px;
  transition:border-color .15s,background .15s;cursor:pointer;position:relative}}
.cell:hover{{border-color:var(--accent)}}
.cell.winner{{border-color:var(--green)!important;background:#0e1f14!important}}
.cell.winner::after{{content:'✓ 选中';position:absolute;top:6px;right:8px;
  font-size:10px;color:var(--green);font-weight:700;font-family:var(--mono)}}
.cell .cell-label{{font-size:10px;color:var(--muted);font-family:var(--mono);
  margin-bottom:5px;display:flex;gap:6px}}
.contrib{{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;font-weight:600}}
.contrib.incremental{{background:#1a2c5a;color:var(--accent)}}
.contrib.significant{{background:#1f3a2a;color:var(--green)}}
.contrib.story-heavy{{background:#3a2e1a;color:var(--yellow)}}
.contrib.foundational{{background:#2a1a3a;color:var(--purple)}}
.cite{{margin:3px 0;padding:4px 7px;background:#090c14;border-radius:4px;font-size:11px}}
.role-tag{{font-family:var(--mono);font-size:10px;padding:1px 5px;border-radius:3px;background:#1e2235;color:var(--purple);margin-right:4px}}
.mv{{margin:2px 0;padding:3px 7px;background:#090c14;border-radius:4px;font-size:11px}}
.mv-tag{{font-family:var(--mono);font-size:11px;color:var(--yellow);margin-right:4px}}
.cite-note{{color:var(--muted);font-size:10px}}
.cite-count{{font-size:10px;font-family:var(--mono);color:var(--muted)}}
.warn{{color:var(--red);font-size:11px;font-family:var(--mono)}}

/* ── VOTE PANEL ── */
.vote-panel{{background:var(--card);border:1px solid var(--border);border-radius:8px;
  padding:16px;margin-top:24px}}
.vote-panel h2{{font-size:14px;color:#fff;margin-bottom:12px}}
.vote-row{{display:flex;align-items:baseline;gap:10px;margin:5px 0;flex-wrap:wrap}}
.vote-field{{font-family:var(--mono);font-size:11px;color:var(--accent);min-width:140px}}
.vote-val{{font-size:12px;color:var(--green);font-weight:700}}
.vote-none{{color:var(--muted);font-size:11px}}
.export-btn{{margin-top:14px;padding:8px 20px;background:var(--accent);border:none;border-radius:6px;
  color:#fff;font-size:13px;font-weight:700;cursor:pointer;transition:.15s}}
.export-btn:hover{{opacity:.85}}
</style>
</head><body>
<div class="wrap">
<h1>Variant E — Editorial &amp; CoreCite 批判优化测试</h1>
<div class="meta">论文 2601.01535 · ReTok / Improving Flexible Image Tokenizers
&nbsp;·&nbsp; E1: 自由批判型 &nbsp;·&nbsp; E2: 结构化批判型 &nbsp;·&nbsp; 3 模型 × 2 变体 = 6 cells</div>

<div class="controls">
  <span class="toggle-label">变体:</span>
  <div class="toggle-group" id="variantToggles"></div>
  &nbsp;
  <span class="toggle-label">模型:</span>
  <div class="toggle-group" id="modelToggles"></div>
</div>
<div style="font-size:11px;color:var(--muted);margin-bottom:14px">
  点击单元格 = 为该字段选中该输出 &nbsp;|&nbsp; 点击 tab 切换显示列 &nbsp;|&nbsp; 下方面板实时汇总你的选择
</div>

<div id="mainGrid"></div>

<div class="vote-panel">
  <h2>📊 选择汇总</h2>
  <div id="voteSummary"></div>
  <button class="export-btn" onclick="exportChoices()">复制结果到剪贴板</button>
</div>
</div>

<script>
const MODELS   = {models_json};
const VARIANTS = {variants_json};
const FIELDS   = {fields_json};
const FLABELS  = {labels_json};
const MNOTES   = {model_notes};
const VLABELS  = {variant_labels};
const CELLS    = {cells_json};

let showVariants = [...VARIANTS];
let showModels   = [...MODELS];
let picks = {{}};  // field -> key (e.g. "e2_gpt52")
FIELDS.forEach(f => picks[f] = null);

function colKey(v,m){{ return v+'_'+m; }}
function cellData(v,m,field){{
  const d = (CELLS[colKey(v,m)]||{{}}).data || {{}};
  return d[field];
}}
function lat(v,m){{ return (CELLS[colKey(v,m)]||{{}}).latency||0; }}

function renderField(v,m,field){{
  const val = cellData(v,m,field);
  if(!val) return '<span class="warn">—</span>';
  if(field==='contribution_type') return `<span class="contrib ${{val}}">${{val}}</span>`;
  if(field==='method_variants' && Array.isArray(val)){{
    return val.map(mv=>`<div class="mv"><span class="mv-tag">${{esc(mv.variant_tag||'')}}</span>${{esc(mv.description||'')}}</div>`).join('');
  }}
  if(field==='core_cite' && Array.isArray(val)){{
    const html = val.map(c=>`<div class="cite">
      <span class="role-tag">${{c.role||'?'}}</span>
      <strong>${{esc((c.title||'').substring(0,60))}}</strong>
      <div class="cite-note">${{esc(c.note||'')}}</div>
    </div>`).join('');
    return html + `<div class="cite-count">${{val.length}} 条引用</div>`;
  }}
  return esc(String(val));
}}

function esc(s){{ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }}

function visibleCols(){{
  const cols=[];
  for(const m of MODELS) for(const v of VARIANTS)
    if(showModels.includes(m) && showVariants.includes(v)) cols.push({{v,m}});
  return cols;
}}

function render(){{
  const cols = visibleCols();
  const n = cols.length;
  const gtc = `grid-template-columns: repeat(${{n}}, 1fr)`;
  let html = '';

  // header row
  html += `<div class="grid-header" style="${{gtc}}">`;
  cols.forEach(({v,m})=>{{
    const isRef = m==='claude46';
    html += `<div class="col-header${{isRef?' ref':''}}">
      <div class="model">${{m}}${{isRef?' <span style="font-size:10px;color:#888">参照</span>':''}}</div>
      <div class="vnote">${{VLABELS[v]||v}}</div>
      <div class="lat">${{lat(v,m).toFixed(1)}}s</div>
    </div>`;
  }});
  html += '</div>';

  // field rows
  FIELDS.forEach(field=>{{
    const label = FLABELS[field]||field;
    html += `<div class="field-row">`;
    html += `<div class="field-row-header"><span>${{label}}</span></div>`;
    html += `<div class="row-cells" style="${{gtc}}">`;
    cols.forEach(({v,m})=>{{
      const key = colKey(v,m);
      const chosen = picks[field]===key;
      html += `<div class="cell${{chosen?' winner':''}}" onclick="pick('${{field}}','${{key}}')" title="${{m}} × ${{v}}">
        <div class="cell-label"><span>${{v.toUpperCase()}}</span><span style="color:#666">×</span><span>${{m}}</span></div>
        ${{renderField(v,m,field)}}
      </div>`;
    }});
    html += '</div></div>';
  }});

  document.getElementById('mainGrid').innerHTML = html;
  renderVotes();
}}

function pick(field, key){{
  picks[field] = picks[field]===key ? null : key;
  render();
}}

function renderVotes(){{
  const s = document.getElementById('voteSummary');
  let html='';
  let done=0;
  FIELDS.forEach(f=>{{
    const k = picks[f];
    html+=`<div class="vote-row"><span class="vote-field">${{FLABELS[f]||f}}</span>`;
    if(k){{ done++; html+=`<span class="vote-val">${{k.replace('_',' × ')}}</span>`; }}
    else html+=`<span class="vote-none">未选</span>`;
    html+='</div>';
  }});
  html+=`<div style="margin-top:10px;font-size:11px;color:var(--muted)">已选 ${{done}}/${{FIELDS.length}} 字段</div>`;
  s.innerHTML=html;
}}

function exportChoices(){{
  const lines = FIELDS.map(f=> `${{f}}: ${{picks[f]||'未选'}}`);
  navigator.clipboard.writeText(lines.join('\\n')).then(()=>alert('已复制到剪贴板'));
}}

// Toggles
function buildToggles(){{
  const vt = document.getElementById('variantToggles');
  VARIANTS.forEach(v=>{{
    const b=document.createElement('div');
    b.className='tbtn on'; b.textContent=VLABELS[v]||v; b.dataset.v=v;
    b.onclick=()=>{{ toggleVariant(v); }};
    vt.appendChild(b);
  }});
  const mt=document.getElementById('modelToggles');
  MODELS.forEach(m=>{{
    const b=document.createElement('div');
    b.className='tbtn on'+(m==='claude46'?' ref':''); b.textContent=m+(MNOTES[m]?' ('+MNOTES[m]+')':'');
    b.dataset.m=m;
    b.onclick=()=>{{ toggleModel(m); }};
    mt.appendChild(b);
  }});
}}

function toggleVariant(v){{
  const idx=showVariants.indexOf(v);
  if(idx>=0&&showVariants.length>1) showVariants.splice(idx,1);
  else if(idx<0) showVariants.push(v);
  document.querySelectorAll('[data-v]').forEach(b=>b.classList.toggle('on',showVariants.includes(b.dataset.v)));
  render();
}}
function toggleModel(m){{
  const idx=showModels.indexOf(m);
  if(idx>=0&&showModels.length>1) showModels.splice(idx,1);
  else if(idx<0) showModels.push(m);
  document.querySelectorAll('[data-m]').forEach(b=>b.classList.toggle('on',showModels.includes(b.dataset.m)));
  render();
}}

buildToggles();
render();
</script>
</body></html>"""

    out = TEST_DIR / "comparison_e.html"
    out.write_text(html)
    print(f"✓ {out}  ({len(html)} bytes)")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--run", action="store_true")
    p.add_argument("--status", action="store_true")
    p.add_argument("--html", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--model", type=str)
    p.add_argument("--variant", type=str)
    args = p.parse_args()
    if args.status: show_status()
    elif args.html: generate_html()
    elif args.run: run_all(force=args.force, model_filter=args.model, variant_filter=args.variant)
    else: p.print_help()
