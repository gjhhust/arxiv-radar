#!/usr/bin/env python3
"""
arxiv-radar Phase 1 G/H Hallucination Test v2
G: No S2 reference list, no anchor instruction -- pure model recall
H: With S2 reference list + "verify before output, delete if not found"

Usage: python3 run_gh_v2.py [--step all|g|h|verify|report] [--paper 2406.07550]
"""

import json, os, sys, re, time, sqlite3, subprocess, urllib.request, urllib.error
from pathlib import Path
from difflib import SequenceMatcher
from datetime import datetime
from typing import Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR   = Path(__file__).parent
RADAR_DIR    = SCRIPT_DIR.parent
DB_PATH      = RADAR_DIR / "data/paper_network.db"
PA_WS        = Path.home() / ".openclaw/workspace-paper-analyst"
PAPERS_DIR   = PA_WS / "papers"
RES_G_DIR    = SCRIPT_DIR / "results_g3"
RES_H_DIR    = SCRIPT_DIR / "results_h3"
STATE_FILE   = SCRIPT_DIR / "state_gh_v2.json"
LOG_FILE     = SCRIPT_DIR / "run_gh_v2.log"

ARXIV_IDS = [
    "2406.07550", "2501.07730", "2503.08685", "2503.10772", "2504.08736",
    "2505.21473", "2505.12053", "2506.05289", "2507.08441", "2511.20565", "2601.01535"
]

WQ_URL    = "http://localhost:4141/messages"
MODEL     = "wq/minimaxm25"
MAX_TOK   = 8192
TIMEOUT   = 300
SIM_PASS  = 0.8   # similarity threshold for "matched"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"done_g": [], "done_h": [], "results": {}}

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def get_s2_refs(arxiv_id: str) -> list:
    """Return list of {arxiv_id, title} from CITES edges in DB."""
    if not DB_PATH.exists():
        log(f"  [WARN] DB not found: {DB_PATH}")
        return []
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("""
            SELECT p.id, p.title FROM paper_edges e
            JOIN papers p ON e.dst_id = p.id
            WHERE e.src_id = ? AND e.edge_type = 'CITES'
            ORDER BY p.title
        """, (arxiv_id,))
        return [{"arxiv_id": r[0] or "", "title": r[1] or ""} for r in cur.fetchall()]
    finally:
        conn.close()

def get_paper_meta(arxiv_id: str) -> dict:
    """Return {title, abstract} from DB."""
    if not DB_PATH.exists():
        return {}
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("SELECT title, abstract FROM papers WHERE id = ?", (arxiv_id,))
        row = cur.fetchone()
        return {"title": row[0] or "", "abstract": row[1] or ""} if row else {}
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# arxiv-fetch
# ---------------------------------------------------------------------------
def arxiv_fetch(arxiv_id: str) -> Optional[dict]:
    """Run arxiv-fetch skill from paper-analyst workspace. Returns JSON dict."""
    script = PA_WS / "skills/arxiv-fetch/scripts/arxiv_fetch.py"
    if not script.exists():
        log(f"  [WARN] arxiv-fetch script not found: {script}")
        return None
    try:
        r = subprocess.run(
            [sys.executable, str(script), arxiv_id],
            capture_output=True, text=True, timeout=180, cwd=str(PA_WS)
        )
        if r.returncode != 0:
            log(f"  [fetch error] {r.stderr[:300]}")
            return None
        return json.loads(r.stdout)
    except subprocess.TimeoutExpired:
        log(f"  [fetch timeout] {arxiv_id}")
        return None
    except Exception as e:
        log(f"  [fetch exception] {e}")
        return None

def read_paper_content(fetch: dict) -> str:
    """Extract paper text for LLM prompt (truncated to ~6000 chars)."""
    MAX_CHARS = 6000
    paper_dir = PA_WS / fetch.get("paper_dir", "")

    if fetch.get("source") == "latex":
        # Find main .tex (has \begin{document})
        for tf in sorted(paper_dir.glob("*.tex")):
            try:
                content = tf.read_text(errors="ignore")
                if r"\begin{document}" in content:
                    return content[:MAX_CHARS]
            except Exception:
                continue
        # Fallback: first .tex
        tex_files = list(paper_dir.glob("*.tex"))
        if tex_files:
            return tex_files[0].read_text(errors="ignore")[:MAX_CHARS]
    else:
        annotated = paper_dir / "paper_annotated.txt"
        if annotated.exists():
            return annotated.read_text(errors="ignore")[:MAX_CHARS]
    return ""

# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------
BASE_FIELDS = """字段要求：

cn_oneliner：一句话，格式「基于[前驱方法]，引入[核心改动]，实现[效果]」
  例1：「基于 Transformer，引入局部窗口注意力机制，将自注意力复杂度从 O(n²) 降至 O(n)」
  例2：「基于 DDPM，将扩散过程迁移至 VAE 潜空间，实现高分辨率图像生成」

cn_abstract：2-4句完整中文摘要，保留核心数值结果，不截断
  例：「本文提出…以解决…问题。通过在…上引入…，模型在…基准上达到…，较基线提升…%。」

contribution_type：严格四选一 incremental / significant / story-heavy / foundational
  例1：在单数据集微调刷点，无架构创新 → incremental
  例2：首次将 Transformer 引入视觉识别并超越 CNN → significant（ViT 级别）
  例3：系统性综述或技术报告，以规模取胜无核心方法创新 → story-heavy

editorial_note：三段连续文字：[前驱方法背景]→[本文核心贡献]→[编辑判断]，80-150字
  例：「BERT 通过掩码语言模型预训练奠定 NLP 迁移学习范式。本文引入动态掩码策略并延长
  训练步数，在多项下游任务取得一致提升（RoBERTa）。改动偏工程层面，创新点单一但扎实，
  验证了训练设置的重要性，是理解预训练因素的重要参考。」

why_read：一句话，说清楚谁应该读 + 能获得什么
  例：「做生成模型训练优化的研究者，可借鉴其噪声调度分析方法」

method_variants：自由格式，列出 base_method:variant_tag 对
  例：[\"attention:local-window\", \"diffusion:latent-space\"]

core_cite：≥10条，每条含 title / arxiv_id（如有）/ role / note
  role 五选一：extends | contrasts | uses | supports | mentions"""

CORE_CITE_G = """  （直接列出你认为该论文引用的最重要文献）"""

CORE_CITE_H = """  确保每条 title 来自文末参考列表，输出前逐条核查，列表中找不到对应条目则删除该条"""

IDEA_FIELD = """
idea：3条研究 idea，综合本文方法/局限、相关领域工作、作者团队历史工作
  每条含：title（研究方向标题）+ why（1-3句，依据和可行性）"""

JSON_SCHEMA = """{
  "arxiv_id": "{arxiv_id}",
  "title": "...",
  "cn_oneliner": "...",
  "cn_abstract": "...",
  "contribution_type": "...",
  "editorial_note": "...",
  "why_read": "...",
  "method_variants": [...],
  "core_cite": [{{"title":"...","arxiv_id":"...","role":"...","note":"..."}}],
  "idea": [{{"title":"...","why":"..."}}]
}"""

def build_prompt_g(arxiv_id: str, title: str, abstract: str, content: str) -> str:
    schema = JSON_SCHEMA.replace("{arxiv_id}", arxiv_id)
    return f"""论文信息：

标题：{title}

摘要：{abstract}

正文（节选）：
{content}

---
{BASE_FIELDS}
{CORE_CITE_G}
{IDEA_FIELD}

输出纯 JSON，不要其他内容：
{schema}"""

def build_prompt_h(arxiv_id: str, title: str, abstract: str, content: str,
                   s2_refs: list) -> str:
    ref_lines = "\n".join(
        f"- {r['title']}{(' | arxiv:' + r['arxiv_id']) if r.get('arxiv_id') else ''}"
        for r in s2_refs
    )
    schema = JSON_SCHEMA.replace("{arxiv_id}", arxiv_id)
    return f"""论文信息：

标题：{title}

摘要：{abstract}

正文（节选）：
{content}

---
{BASE_FIELDS}
{CORE_CITE_H}
{IDEA_FIELD}

输出纯 JSON，不要其他内容：
{schema}

---
参考列表（core_cite 须来自其中）：
{ref_lines}"""

# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------
def call_llm(prompt: str) -> Optional[str]:
    payload = json.dumps({
        "model": MODEL,
        "max_tokens": MAX_TOK,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    req = urllib.request.Request(
        WQ_URL, data=payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read())
        for block in data.get("content", []):
            if block.get("type") == "text":
                return block["text"]
        return None
    except urllib.error.URLError as e:
        log(f"  [llm url error] {e}")
        return None
    except Exception as e:
        log(f"  [llm error] {type(e).__name__}: {e}")
        return None

# ---------------------------------------------------------------------------
# JSON parsing (4-pass)
# ---------------------------------------------------------------------------
def parse_json(text: str) -> Optional[dict]:
    def try_parse(s):
        try:
            return json.loads(s)
        except Exception:
            return None

    # Pass 1: direct
    r = try_parse(text)
    if r: return r

    # Pass 2: extract largest {} block
    m = re.search(r'(\{[\s\S]*\})', text)
    if m:
        r = try_parse(m.group(1))
        if r: return r

    # Pass 3: normalize CJK quotes
    cleaned = (text
               .replace('\u201c', '"').replace('\u201d', '"')
               .replace('\u2018', "'").replace('\u2019', "'"))
    m = re.search(r'(\{[\s\S]*\})', cleaned)
    if m:
        r = try_parse(m.group(1))
        if r: return r

    # Pass 4: strip trailing commas
    try:
        fixed = re.sub(r',\s*([}\]])', r'\1', m.group(1) if m else text)
        r = try_parse(fixed)
        if r: return r
    except Exception:
        pass

    return None

# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def sim(a: str, b: str) -> float:
    a2 = re.sub(r'[^\w\s]', ' ', a.lower())
    b2 = re.sub(r'[^\w\s]', ' ', b.lower())
    return SequenceMatcher(None, a2, b2).ratio()

def verify(core_cite: list, s2_refs: list) -> list:
    ref_titles = [r["title"] for r in s2_refs if r.get("title")]
    out = []
    for cite in core_cite:
        title = cite.get("title", "")
        if not title:
            out.append({"title": "", "best_score": 0.0, "best_match": "", "pass": False})
            continue
        best_score, best_match = 0.0, ""
        for rt in ref_titles:
            s = sim(title, rt)
            if s > best_score:
                best_score, best_match = s, rt
        out.append({
            "title": title,
            "best_score": round(best_score, 3),
            "best_match": best_match,
            "pass": best_score >= SIM_PASS
        })
    return out

# ---------------------------------------------------------------------------
# Main test loop
# ---------------------------------------------------------------------------
def run_paper(arxiv_id: str, scheme: str, state: dict) -> Optional[dict]:
    done_key = f"done_{scheme.lower()}"
    if arxiv_id in state.get(done_key, []):
        log(f"  [SKIP] {arxiv_id} scheme {scheme} already done")
        return state["results"].get(arxiv_id, {}).get(scheme)

    # Get metadata
    meta = get_paper_meta(arxiv_id)
    title = meta.get("title", arxiv_id)
    abstract = meta.get("abstract", "")

    # Fetch paper
    log(f"  Fetching {arxiv_id}...")
    fetch = arxiv_fetch(arxiv_id)
    content = read_paper_content(fetch) if fetch else ""
    if not content:
        log(f"  [WARN] no content for {arxiv_id}, using abstract only")

    # S2 refs
    s2_refs = get_s2_refs(arxiv_id)
    log(f"  S2 refs: {len(s2_refs)}")

    # Build prompt
    if scheme == "G":
        prompt = build_prompt_g(arxiv_id, title, abstract, content)
    else:
        prompt = build_prompt_h(arxiv_id, title, abstract, content, s2_refs)

    # Call LLM
    log(f"  Calling LLM ({MODEL}) scheme {scheme}...")
    t0 = time.time()
    raw = call_llm(prompt)
    elapsed = round(time.time() - t0, 1)
    log(f"  LLM done in {elapsed}s")

    if not raw:
        log(f"  [ERROR] LLM returned None")
        return None

    result = parse_json(raw)
    if not result:
        log(f"  [ERROR] JSON parse failed")
        # Save raw for debug
        raw_path = (RES_G_DIR if scheme == "G" else RES_H_DIR) / f"{arxiv_id}_raw.txt"
        raw_path.write_text(raw)
        return None

    # Verify
    sims = verify(result.get("core_cite", []), s2_refs)
    matched = sum(1 for v in sims if v["pass"])
    total = len(sims)
    rate = round(matched / max(total, 1), 3)

    result.update({
        "_scheme": scheme,
        "_model": MODEL,
        "_verified_at": datetime.now().strftime("%Y-%m-%d"),
        "_s2_ref_count": len(s2_refs),
        "_similarities": sims,
        "_matched": matched,
        "_total": total,
        "_pass_rate": rate,
        "_elapsed_s": elapsed
    })

    # Save
    out_dir = RES_G_DIR if scheme == "G" else RES_H_DIR
    out_path = out_dir / f"{arxiv_id}.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    log(f"  {scheme}: {matched}/{total} matched ({rate*100:.0f}%) → {out_path}")

    return {"matched": matched, "total": total, "rate": rate, "path": str(out_path)}

def run_all():
    RES_G_DIR.mkdir(exist_ok=True)
    RES_H_DIR.mkdir(exist_ok=True)
    state = load_state()

    for arxiv_id in ARXIV_IDS:
        log(f"\n{'='*55}")
        log(f"PAPER: {arxiv_id}")

        for scheme in ["G", "H"]:
            res = run_paper(arxiv_id, scheme, state)
            if res:
                if arxiv_id not in state["results"]:
                    state["results"][arxiv_id] = {}
                state["results"][arxiv_id][scheme] = res
                done_key = f"done_{scheme.lower()}"
                if arxiv_id not in state.get(done_key, []):
                    state.setdefault(done_key, []).append(arxiv_id)
                save_state(state)
            time.sleep(4)   # brief pause between G and H

        time.sleep(6)   # pause between papers

    log("\nAll papers complete.")
    generate_html(state["results"])
    log(f"Report: {SCRIPT_DIR}/report_gh_v2.html")

# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------
def generate_html(results: dict):
    today = datetime.now().strftime("%Y-%m-%d")

    # Aggregate stats
    g_rates, h_rates = [], []
    rows = []
    for aid in ARXIV_IDS:
        r = results.get(aid, {})
        g = r.get("G", {})
        h = r.get("H", {})
        g_rate = g.get("rate", None)
        h_rate = h.get("rate", None)
        if g_rate is not None: g_rates.append(g_rate)
        if h_rate is not None: h_rates.append(h_rate)

        # Load detail
        g_detail, h_detail = [], []
        if g.get("path"):
            try:
                gj = json.loads(Path(g["path"]).read_text())
                g_detail = gj.get("_similarities", [])
            except Exception:
                pass
        if h.get("path"):
            try:
                hj = json.loads(Path(h["path"]).read_text())
                h_detail = hj.get("_similarities", [])
            except Exception:
                pass

        rows.append({
            "arxiv_id": aid,
            "g_matched": g.get("matched","?"), "g_total": g.get("total","?"),
            "g_rate": g_rate, "g_detail": g_detail,
            "h_matched": h.get("matched","?"), "h_total": h.get("total","?"),
            "h_rate": h_rate, "h_detail": h_detail,
            "g_err": "error" in g, "h_err": "error" in h
        })

    avg_g = round(sum(g_rates)/len(g_rates)*100, 1) if g_rates else 0
    avg_h = round(sum(h_rates)/len(h_rates)*100, 1) if h_rates else 0

    def rate_color(r):
        if r is None: return "#94a3b8"
        if r >= 0.8: return "#4ade80"
        if r >= 0.5: return "#facc15"
        return "#f87171"

    def cite_rows(detail, scheme):
        if not detail:
            return '<tr><td colspan="4" style="color:#94a3b8;text-align:center">no data</td></tr>'
        html = ""
        for v in detail:
            sc = v.get("best_score", 0)
            passed = v.get("pass", False)
            color = "#4ade80" if passed else "#f87171"
            html += f"""<tr>
              <td style="color:#e2e8f0;font-size:11px">{v.get('title','')[:60]}</td>
              <td style="color:{color};font-weight:600;text-align:center">{sc:.2f}</td>
              <td style="color:{color};text-align:center">{'✓' if passed else '✗'}</td>
              <td style="color:#94a3b8;font-size:10px">{v.get('best_match','')[:50]}</td>
            </tr>"""
        return html

    paper_sections = ""
    for row in rows:
        g_pct = f"{row['g_rate']*100:.0f}%" if row['g_rate'] is not None else "ERR"
        h_pct = f"{row['h_rate']*100:.0f}%" if row['h_rate'] is not None else "ERR"
        gc = rate_color(row['g_rate'])
        hc = rate_color(row['h_rate'])
        paper_sections += f"""
<div class="paper-card">
  <div class="paper-header">
    <span class="arxiv-id">{row['arxiv_id']}</span>
    <span class="scheme-badge" style="background:rgba(108,142,247,.15);color:#6c8ef7">
      G: <strong style="color:{gc}">{row['g_matched']}/{row['g_total']} ({g_pct})</strong>
    </span>
    <span class="scheme-badge" style="background:rgba(167,139,250,.15);color:#a78bfa">
      H: <strong style="color:{hc}">{row['h_matched']}/{row['h_total']} ({h_pct})</strong>
    </span>
    <button onclick="toggle('{row['arxiv_id']}')" class="expand-btn">展开 cite 详情</button>
  </div>
  <div id="detail-{row['arxiv_id']}" style="display:none;margin-top:12px">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
      <div>
        <div style="color:#6c8ef7;font-weight:700;font-size:12px;margin-bottom:8px">
          Scheme G — {row['g_matched']}/{row['g_total']} matched (无参考列表)
        </div>
        <table class="cite-table">
          <tr><th>title</th><th>score</th><th>pass</th><th>best match</th></tr>
          {cite_rows(row['g_detail'], 'G')}
        </table>
      </div>
      <div>
        <div style="color:#a78bfa;font-weight:700;font-size:12px;margin-bottom:8px">
          Scheme H — {row['h_matched']}/{row['h_total']} matched (有参考列表+核查)
        </div>
        <table class="cite-table">
          <tr><th>title</th><th>score</th><th>pass</th><th>best match</th></tr>
          {cite_rows(row['h_detail'], 'H')}
        </table>
      </div>
    </div>
  </div>
</div>"""

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>G/H Hallucination Test v2 — {today}</title>
<style>
  :root{{--bg:#0f1117;--surface:#1a1d27;--surface2:#22263a;--border:#2e3350;
    --accent:#6c8ef7;--accent2:#a78bfa;--green:#4ade80;--yellow:#facc15;
    --red:#f87171;--text:#e2e8f0;--muted:#94a3b8}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,sans-serif;background:var(--bg);color:var(--text);font-size:14px;padding:32px 24px}}
  .container{{max-width:1200px;margin:0 auto}}
  h1{{font-size:24px;color:var(--accent);margin-bottom:8px}}
  .sub{{color:var(--muted);font-size:13px;margin-bottom:32px}}
  .stat-row{{display:flex;gap:16px;margin-bottom:32px;flex-wrap:wrap}}
  .stat{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px 24px;text-align:center;min-width:160px}}
  .stat-value{{font-size:36px;font-weight:800}}
  .stat-label{{color:var(--muted);font-size:12px;margin-top:4px}}
  .section-title{{font-size:17px;font-weight:700;margin:24px 0 14px;color:var(--accent2)}}
  .paper-card{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px;margin-bottom:10px}}
  .paper-header{{display:flex;align-items:center;gap:12px;flex-wrap:wrap}}
  .arxiv-id{{font-family:monospace;font-size:13px;color:var(--accent);font-weight:700}}
  .scheme-badge{{padding:4px 12px;border-radius:8px;font-size:12px}}
  .expand-btn{{margin-left:auto;background:var(--surface2);border:1px solid var(--border);
    color:var(--muted);padding:4px 12px;border-radius:6px;cursor:pointer;font-size:12px}}
  .expand-btn:hover{{color:var(--text)}}
  .cite-table{{width:100%;border-collapse:collapse;font-size:11px}}
  .cite-table th{{background:var(--surface2);color:var(--muted);padding:6px 8px;text-align:left;border-bottom:1px solid var(--border)}}
  .cite-table td{{padding:5px 8px;border-bottom:1px solid var(--border);vertical-align:top}}
  .conclusion{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px;margin-top:24px}}
  .hl{{border-left:3px solid;padding:10px 14px;border-radius:0 8px 8px 0;margin:10px 0;font-size:13px}}
  .hl-g{{border-color:var(--green);background:rgba(74,222,128,.08)}}
  .hl-y{{border-color:var(--yellow);background:rgba(250,204,21,.08)}}
  .hl-b{{border-color:var(--accent);background:rgba(108,142,247,.08)}}
</style>
</head>
<body>
<div class="container">
<h1>📊 G/H Hallucination Test v2</h1>
<div class="sub">model: {MODEL} · 11 papers · threshold: {SIM_PASS} · {today}</div>

<div class="stat-row">
  <div class="stat">
    <div class="stat-value" style="color:{'#4ade80' if avg_g>=80 else '#facc15' if avg_g>=50 else '#f87171'}">{avg_g}%</div>
    <div class="stat-label">Scheme G avg pass rate<br>(无参考列表)</div>
  </div>
  <div class="stat">
    <div class="stat-value" style="color:{'#4ade80' if avg_h>=80 else '#facc15' if avg_h>=50 else '#f87171'}">{avg_h}%</div>
    <div class="stat-label">Scheme H avg pass rate<br>(有参考列表+核查)</div>
  </div>
  <div class="stat">
    <div class="stat-value" style="color:{'#4ade80' if avg_h>avg_g else '#f87171'}">
      {'+' if avg_h>=avg_g else ''}{round(avg_h-avg_g,1)}%
    </div>
    <div class="stat-label">H vs G 差值</div>
  </div>
  <div class="stat">
    <div class="stat-value" style="color:var(--accent)">{len(ARXIV_IDS)}</div>
    <div class="stat-label">papers tested</div>
  </div>
</div>

<div class="conclusion">
  <div class="hl hl-g"><strong>G（无锚点）：</strong>模型完全依赖训练记忆生成 core_cite，pass rate = {avg_g}%</div>
  <div class="hl hl-b"><strong>H（有锚点）：</strong>S2 列表 + 逐条核查指令，pass rate = {avg_h}%</div>
  <div class="hl hl-y"><strong>结论：</strong>{"H 显著优于 G，S2 锚点对幻觉抑制有实质效果" if avg_h - avg_g > 10 else "H 与 G 差距在 10% 以内，S2 锚点效果有限" if abs(avg_h - avg_g) <= 10 else "G 意外优于 H，需人工审查"}</div>
</div>

<div class="section-title">📋 每篇论文详情</div>
{paper_sections}
</div>

<script>
function toggle(id) {{
  const el = document.getElementById('detail-' + id);
  const btn = event.target;
  if (el.style.display === 'none') {{
    el.style.display = 'block';
    btn.textContent = '收起';
  }} else {{
    el.style.display = 'none';
    btn.textContent = '展开 cite 详情';
  }}
}}
</script>
</body></html>"""

    out = SCRIPT_DIR / "report_gh_v2.html"
    out.write_text(html, encoding="utf-8")
    log(f"HTML saved: {out}")

# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", default="all",
                        choices=["all", "g", "h", "report"])
    parser.add_argument("--paper", default=None)
    args = parser.parse_args()

    if args.step == "report":
        state = load_state()
        generate_html(state["results"])
    else:
        if args.paper:
            ARXIV_IDS[:] = [args.paper]
        run_all()
