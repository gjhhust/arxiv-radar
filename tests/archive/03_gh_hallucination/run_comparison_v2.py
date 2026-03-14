#!/usr/bin/env python3
"""
arxiv-radar Comparison Test v3 — H vs I-a (fair comparison)

Design:
  Both H and I-a use the paper-analyst agent (same model, same context).
  The ONLY difference is how the reference list is delivered:
    H:   Python pre-queries DB → embeds formatted list in prompt
    I-a: Prompt contains the sqlite3 command → agent executes it at runtime

  Both use IDENTICAL SQL (SQL_REFS_QUERY). This guarantees same data source.

Usage:
  # Step 1: Build task strings for all 11 papers
  python3 run_comparison_v2.py --step build

  # Step 2: Spawn sessions (Mox runs this via sessions_spawn tool, using tasks_v2/)
  #         → collect result json paths, save to results_v2/{arxiv_id}_H.json
  #                                              results_v2/{arxiv_id}_Ia.json

  # Step 3: Verify all results and generate HTML
  python3 run_comparison_v2.py --step verify
  python3 run_comparison_v2.py --step report
"""

import json, re, sqlite3, argparse
from pathlib import Path
from difflib import SequenceMatcher
from datetime import datetime
from typing import Optional

# ─── Paths ──────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
RADAR_DIR  = SCRIPT_DIR.parent
DB_PATH    = RADAR_DIR / "data/paper_network.db"
TASKS_DIR  = SCRIPT_DIR / "tasks_v2"
RES_DIR    = SCRIPT_DIR / "results_v2"
STATE_FILE = SCRIPT_DIR / "state_v2.json"
TMPL_H     = SCRIPT_DIR / "prompt_H_template.txt"
TMPL_IA    = SCRIPT_DIR / "prompt_Ia_template.txt"
REPORT_OUT = SCRIPT_DIR / "report_v2.html"

# ─── SQL (single source of truth for both H list and I-a command) ─────────
SQL_REFS_QUERY = (
    "SELECT p.title, p.id FROM paper_edges e "
    "JOIN papers p ON e.dst_id = p.id "
    "WHERE e.src_id = '{arxiv_id}' AND e.edge_type = 'CITES' "
    "ORDER BY p.title"
)
# H format: each ref as "- {title} | arxiv:{id}"
# I-a format: full sqlite3 command with arxiv_id substituted

ARXIV_IDS = [
    "2406.07550","2501.07730","2503.08685","2503.10772","2504.08736",
    "2505.21473","2505.12053","2506.05289","2507.08441","2511.20565","2601.01535"
]
SIM_PASS = 0.8

# ─── DB helpers ──────────────────────────────────────────────────────────────
def get_paper_meta(arxiv_id: str) -> dict:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("SELECT title, abstract FROM papers WHERE id = ?", (arxiv_id,))
        row = cur.fetchone()
        return {"title": row[0] or "", "abstract": row[1] or ""} if row else {}
    finally:
        conn.close()

def get_refs(arxiv_id: str) -> list:
    """Returns list of {title, id} using SQL_REFS_QUERY — single source for H and I-a."""
    sql = SQL_REFS_QUERY.replace("{arxiv_id}", arxiv_id)
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(sql)
        return [{"title": r[0] or "", "id": r[1] or ""} for r in cur.fetchall()]
    finally:
        conn.close()

def format_ref_list(refs: list) -> str:
    """Format refs for H prompt embedding. Mirrors sqlite3 column output."""
    return "\n".join(
        f"- {r['title']}{' | arxiv:' + r['id'] if r['id'] else ''}"
        for r in refs
    )

def format_sqlite3_cmd(arxiv_id: str) -> str:
    """Format the I-a sqlite3 command. Uses the same SQL_REFS_QUERY."""
    sql = SQL_REFS_QUERY.replace("{arxiv_id}", arxiv_id)
    return f'sqlite3 {DB_PATH} "{sql}"'

# ─── Task builders ───────────────────────────────────────────────────────────
def build_task_H(arxiv_id: str, title: str, refs: list) -> str:
    tmpl = TMPL_H.read_text(encoding="utf-8")
    ref_list = format_ref_list(refs)
    return (tmpl
            .replace("{arxiv_id}", arxiv_id)
            .replace("{paper_title}", title)
            .replace("{s2_ref_list}", ref_list))

def build_task_Ia(arxiv_id: str, title: str) -> str:
    tmpl = TMPL_IA.read_text(encoding="utf-8")
    cmd = format_sqlite3_cmd(arxiv_id)
    # Replace the generic command line in the template with the filled one
    return (tmpl
            .replace("{arxiv_id}", arxiv_id)
            .replace("{paper_title}", title)
            .replace(
                'sqlite3 /Users/lanlanlan/.openclaw/workspace/skills/arxiv-radar/data/paper_network.db '
                '"SELECT p.title, p.id FROM paper_edges e JOIN papers p ON e.dst_id = p.id '
                'WHERE e.src_id = \'{arxiv_id}\' AND e.edge_type = \'CITES\' ORDER BY p.title"',
                cmd
            ))

# ─── Step 1: build ───────────────────────────────────────────────────────────
def step_build():
    TASKS_DIR.mkdir(exist_ok=True)
    manifest = {}
    for aid in ARXIV_IDS:
        meta = get_paper_meta(aid)
        title = meta.get("title", aid)
        refs  = get_refs(aid)
        print(f"[{aid}] title={title[:50]!r}  refs={len(refs)}")

        task_h  = build_task_H(aid, title, refs)
        task_ia = build_task_Ia(aid, title)

        path_h  = TASKS_DIR / f"H_{aid}.txt"
        path_ia = TASKS_DIR / f"Ia_{aid}.txt"
        path_h.write_text(task_h,  encoding="utf-8")
        path_ia.write_text(task_ia, encoding="utf-8")

        manifest[aid] = {
            "title":       title,
            "ref_count":   len(refs),
            "task_H":      str(path_h),
            "task_Ia":     str(path_ia),
            "result_H":    str(RES_DIR / f"{aid}_H.json"),
            "result_Ia":   str(RES_DIR / f"{aid}_Ia.json"),
        }

    manifest_path = TASKS_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"\nBuilt {len(manifest)} paper task pairs → {TASKS_DIR}")
    print(f"Manifest: {manifest_path}")
    print(f"\nNext: Mox spawns paper-analyst sessions using task files in {TASKS_DIR}")
    print("  For each paper: spawn H session + Ia session")
    print("  Sessions write results to papers/<id>/results_YYYYMMDD_H.json / _Ia.json")
    print("  Then run: python3 run_comparison_v2.py --step verify")

# ─── Step 2: verify ──────────────────────────────────────────────────────────
def sim(a: str, b: str) -> float:
    a2 = re.sub(r'[^\w\s]', ' ', a.lower())
    b2 = re.sub(r'[^\w\s]', ' ', b.lower())
    return SequenceMatcher(None, a2, b2).ratio()

def verify_one(data: dict, refs: list) -> dict:
    ref_titles = [r["title"] for r in refs if r["title"]]
    cites = data.get("core_cite", [])
    sims = []
    for c in cites:
        t = c.get("title", "")
        best, bm = 0.0, ""
        for rt in ref_titles:
            s = sim(t, rt)
            if s > best: best, bm = s, rt
        sims.append({"title": t, "best_score": round(best, 3),
                     "best_match": bm, "pass": best >= SIM_PASS})
    matched = sum(1 for v in sims if v["pass"])
    total   = len(sims)
    return {
        "_similarities": sims,
        "_matched": matched,
        "_total":   total,
        "_pass_rate": round(matched / max(total, 1), 3),
        "_verified_by": "Mox",
        "_verified_at": datetime.now().strftime("%Y-%m-%d"),
        "_ref_count": len(ref_titles),
    }

def find_result(arxiv_id: str, scheme: str) -> Optional[Path]:
    """Search common locations for result file."""
    suffix = f"results_20260314_{scheme}.json"
    roots = [
        Path.home() / ".Meg-Agent/workspace/papers",
        Path.home() / ".openclaw/workspace/papers",
        Path.home() / ".openclaw/workspace-paper-analyst/papers",
        Path.home() / "workspace/papers",
    ]
    for root in roots:
        p = root / arxiv_id / suffix
        if p.exists(): return p
    return None

def step_verify():
    RES_DIR.mkdir(exist_ok=True)
    manifest_path = TASKS_DIR / "manifest.json"
    if not manifest_path.exists():
        print("[ERR] Run --step build first"); return
    manifest = json.loads(manifest_path.read_text())

    state = {}
    for aid in ARXIV_IDS:
        refs = get_refs(aid)
        state[aid] = {}
        for scheme in ["H", "Ia"]:
            path = find_result(aid, scheme)
            if not path:
                print(f"[MISSING] {aid} {scheme}")
                continue
            try:
                data = json.loads(path.read_text())
            except Exception as e:
                print(f"[ERR] {aid} {scheme}: {e}"); continue

            v = verify_one(data, refs)
            data.update(v)
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

            dst = RES_DIR / f"{aid}_{scheme}.json"
            dst.write_text(json.dumps(data, indent=2, ensure_ascii=False))

            state[aid][scheme] = {
                "matched": v["_matched"],
                "total":   v["_total"],
                "rate":    v["_pass_rate"],
                "path":    str(dst),
            }
            print(f"[OK] {aid} {scheme}: {v['_matched']}/{v['_total']} "
                  f"= {v['_pass_rate']*100:.0f}%  refs:{v['_ref_count']}")

    STATE_FILE.write_text(json.dumps({"results": state}, indent=2, ensure_ascii=False))
    print(f"\nState saved: {STATE_FILE}")
    for scheme in ["H", "Ia"]:
        rates = [state[a][scheme]["rate"] for a in ARXIV_IDS
                 if scheme in state.get(a,{}) and state[a][scheme]["total"] > 0]
        if rates:
            avg = round(sum(rates)/len(rates)*100, 1)
            print(f"  {scheme} avg: {avg}%  ({len(rates)} papers)")

# ─── Step 3: report ──────────────────────────────────────────────────────────
def step_report():
    if not STATE_FILE.exists():
        print("[ERR] Run --step verify first"); return
    state = json.loads(STATE_FILE.read_text()).get("results", {})

    def load(path):
        try: return json.loads(Path(path).read_text())
        except: return None

    def rc(r):
        if r is None: return "#94a3b8"
        if r >= 0.8: return "#4ade80"
        if r >= 0.5: return "#facc15"
        return "#f87171"

    def esc(s): return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

    def render_mv(mv):
        if not mv: return "<em style='color:#94a3b8'>[]</em>"
        if isinstance(mv, list) and mv and isinstance(mv[0], dict):
            rows = "".join(
                f'<tr><td class="mv-tag">{esc(m.get("tag",""))}</td>'
                f'<td class="mv-note">{esc(m.get("note",""))}</td></tr>'
                for m in mv
            )
            return f'<table class="mv-table"><thead><tr><th>tag</th><th>note</th></tr></thead><tbody>{rows}</tbody></table>'
        # old string format fallback
        items = "".join(f'<span class="mv-badge">{esc(str(x))}</span>' for x in mv)
        return f'<div>{items}</div>'

    def render_cites(cites, sims):
        if not cites: return "<em style='color:#94a3b8'>no core_cite</em>"
        sm = {v["title"]: v for v in sims}
        rows = ""
        for c in cites:
            t = c.get("title",""); sv = sm.get(t,{})
            sc = sv.get("best_score",0); passed = sv.get("pass",False)
            col = rc(sc if sc else None)
            rows += (f'<tr><td class="ct" title="{esc(t)}">{esc(t)}</td>'
                     f'<td class="ci">{esc(c.get("arxiv_id",""))}</td>'
                     f'<td><span class="rb rb-{esc(c.get("role",""))}">{esc(c.get("role",""))}</span></td>'
                     f'<td class="cn">{esc(c.get("note",""))}</td>'
                     f'<td style="color:{col};font-weight:700;text-align:center">{sc:.2f}</td>'
                     f'<td class="cm" title="{esc(sv.get("best_match",""))}">'
                     f'{esc(sv.get("best_match","")[:55])}</td></tr>')
        m = sum(1 for v in sims if v.get("pass")); tot = len(sims)
        return (f'<div class="cite-hdr">core_cite — '
                f'<span style="color:{rc(m/max(tot,1))};font-weight:700">{m}/{tot} verified</span></div>'
                f'<div style="overflow-x:auto"><table class="ct-table">'
                f'<thead><tr><th>title</th><th>arxiv_id</th><th>role</th>'
                f'<th>note</th><th>score</th><th>best S2 match</th></tr></thead>'
                f'<tbody>{rows}</tbody></table></div>')

    def render_ideas(ideas):
        if not ideas: return ""
        html = '<div class="field"><div class="fl">ideas</div><div class="ideas">'
        for i, x in enumerate(ideas, 1):
            html += (f'<div class="idea"><span class="idea-n">💡{i}</span> '
                     f'<strong>{esc(x.get("title",""))}</strong>'
                     f'<p>{esc(x.get("why",""))}</p></div>')
        return html + "</div></div>"

    def fld(label, val):
        if not val: return ""
        return (f'<div class="field"><div class="fl">{label}</div>'
                f'<div class="fv">{esc(str(val))}</div></div>')

    CT_BG = {"incremental":"#94a3b833","significant":"#4ade8033",
             "foundational":"#a78bfa33","story-heavy":"#fb923c33"}

    def panel(data, scheme, color):
        if not data:
            return (f'<div class="panel" style="border-color:{color}">'
                    f'<div class="ph" style="color:{color}">{scheme}</div>'
                    f'<div class="no-data">no data / parse failed</div></div>')
        m=data.get("_matched",0); tot=data.get("_total",0)
        sims=data.get("_similarities",[])
        ct=data.get("contribution_type",""); ct_bg=CT_BG.get(ct,"")
        rate_col=rc(m/max(tot,1) if tot else None)
        flds = (fld("cn_oneliner",data.get("cn_oneliner")) +
                fld("cn_abstract",data.get("cn_abstract")) +
                fld("editorial_note",data.get("editorial_note")) +
                fld("why_read",data.get("why_read")) +
                f'<div class="field"><div class="fl">method_variants</div>'
                f'<div class="fv">{render_mv(data.get("method_variants"))}</div></div>' +
                render_cites(data.get("core_cite",[]),sims) +
                render_ideas(data.get("idea",[])))
        return (f'<div class="panel" style="border-color:{color}">'
                f'<div class="ph" style="color:{color}">{scheme}'
                f'<span style="font-size:11px;margin-left:10px;color:{rate_col}">'
                f'{m}/{tot} verified</span>'
                f'<span class="ct-b" style="background:{ct_bg}">{esc(ct)}</span>'
                f'</div>{flds}</div>')

    cards = ""
    for aid in ARXIV_IDS:
        s = state.get(aid, {})
        hd = load(s.get("H",{}).get("path")) if s.get("H") else None
        id_ = load(s.get("Ia",{}).get("path")) if s.get("Ia") else None
        title = (hd or id_ or {}).get("title", aid)
        hm=s.get("H",{}).get("matched","?"); ht=s.get("H",{}).get("total","?")
        im=s.get("Ia",{}).get("matched","?"); it=s.get("Ia",{}).get("total","?")
        hr=s.get("H",{}).get("rate"); ir=s.get("Ia",{}).get("rate")
        cid = aid.replace(".","_")
        cards += f"""<div class="card" id="c-{cid}">
  <div class="card-hdr" onclick="toggle('{cid}')">
    <span class="ab">{aid}</span>
    <span class="ct2">{esc(title[:70]+'…' if len(title)>70 else title)}</span>
    <span style="color:{rc(hr)}">H:{hm}/{ht}</span>
    <span style="color:{rc(ir)}">I-a:{im}/{it}</span>
    <span class="ei">▼</span>
  </div>
  <div class="card-body" id="b-{cid}" style="display:none">
    <div class="grid2">
      {panel(hd, "Scheme H — pre-embedded list", "#6c8ef7")}
      {panel(id_, "Scheme I-a — agent self-query", "#4ade80")}
    </div>
  </div>
</div>"""

    h_rates  = [state[a]["H"]["rate"]  for a in ARXIV_IDS if "H"  in state.get(a,{}) and state[a]["H"]["total"]>0]
    ia_rates = [state[a]["Ia"]["rate"] for a in ARXIV_IDS if "Ia" in state.get(a,{}) and state[a]["Ia"]["total"]>0]
    avg_h  = round(sum(h_rates) /len(h_rates) *100,1) if h_rates  else 0
    avg_ia = round(sum(ia_rates)/len(ia_rates)*100,1) if ia_rates else 0
    diff   = round(avg_ia - avg_h, 1)
    today  = datetime.now().strftime("%Y-%m-%d")

    html = f"""<!DOCTYPE html><html lang="zh"><head>
<meta charset="UTF-8"><title>H vs I-a Comparison v3 — {today}</title>
<style>
:root{{--bg:#0f1117;--sf:#1a1d27;--sf2:#22263a;--br:#2e3350;--ac:#6c8ef7;
  --gn:#4ade80;--ye:#facc15;--rd:#f87171;--tx:#e2e8f0;--mt:#94a3b8}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,sans-serif;background:var(--bg);color:var(--tx);
  padding:28px 20px;font-size:13px}}
.wrap{{max-width:1400px;margin:0 auto}}
h1{{font-size:21px;color:var(--ac);margin-bottom:5px}}
.sub{{color:var(--mt);font-size:12px;margin-bottom:24px}}
.stats{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:22px}}
.st{{background:var(--sf);border:1px solid var(--br);border-radius:10px;
  padding:16px 20px;text-align:center;min-width:140px}}
.sv{{font-size:30px;font-weight:800}}.sl{{color:var(--mt);font-size:11px;margin-top:3px}}
.note-box{{background:var(--sf);border:1px solid var(--br);border-radius:8px;
  padding:12px 16px;margin-bottom:20px;font-size:12px;color:#b0bec5;line-height:1.7}}
.note-box strong{{color:var(--tx)}}
.card{{background:var(--sf);border:1px solid var(--br);border-radius:9px;margin-bottom:8px;overflow:hidden}}
.card-hdr{{display:flex;align-items:center;gap:10px;padding:12px 16px;
  cursor:pointer;flex-wrap:wrap;user-select:none}}
.card-hdr:hover{{background:var(--sf2)}}
.ab{{font-family:monospace;font-size:12px;color:var(--ac);background:rgba(108,142,247,.1);
  padding:2px 9px;border-radius:5px;white-space:nowrap}}
.ct2{{flex:1;min-width:180px}}
.ei{{color:var(--mt);font-size:11px;transition:transform .2s}}
.ei.open{{transform:rotate(180deg)}}
.card-body{{padding:14px 16px;border-top:1px solid var(--br)}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
@media(max-width:900px){{.grid2{{grid-template-columns:1fr}}}}
.panel{{border:1px solid var(--br);border-radius:7px;padding:14px;background:var(--sf2)}}
.ph{{font-size:12px;font-weight:700;margin-bottom:12px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
.ct-b{{padding:2px 8px;border-radius:4px;font-size:11px;margin-left:auto;border:1px solid transparent}}
.field{{margin-bottom:12px}}
.fl{{font-size:10px;font-weight:700;color:var(--mt);text-transform:uppercase;
  letter-spacing:.05em;margin-bottom:4px}}
.fv{{line-height:1.6;color:var(--tx)}}
.no-data{{color:var(--mt);padding:12px}}
.mv-table{{width:100%;border-collapse:collapse;font-size:11px}}
.mv-table th{{background:rgba(0,0,0,.2);padding:4px 8px;text-align:left;
  color:var(--mt);border-bottom:1px solid var(--br)}}
.mv-table td{{padding:4px 8px;border-bottom:1px solid rgba(46,51,80,.4);vertical-align:top}}
.mv-tag{{font-family:monospace;color:var(--ac)}}
.mv-note{{color:#b0bec5}}
.mv-badge{{background:rgba(108,142,247,.12);color:var(--ac);border-radius:4px;
  padding:2px 7px;font-size:11px;font-family:monospace;margin:2px;display:inline-block}}
.cite-hdr{{font-size:11px;font-weight:600;margin-bottom:6px}}
.ct-table{{width:100%;border-collapse:collapse;font-size:10px;margin-bottom:12px}}
.ct-table thead{{background:rgba(0,0,0,.3)}}
.ct-table th{{padding:5px 8px;text-align:left;color:var(--mt);border-bottom:1px solid var(--br);white-space:nowrap}}
.ct-table td{{padding:5px 8px;border-bottom:1px solid rgba(46,51,80,.4);vertical-align:top}}
.ct{{max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;cursor:default}}
.ci{{font-family:monospace;color:var(--ac);font-size:10px;white-space:nowrap}}
.cn{{color:#b0bec5;max-width:180px}}
.cm{{color:var(--mt);font-size:10px;max-width:160px;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap;cursor:default}}
.rb{{padding:2px 6px;border-radius:4px;font-size:10px;font-weight:600}}
.rb-extends{{background:rgba(74,222,128,.15);color:#4ade80}}
.rb-contrasts{{background:rgba(248,113,113,.15);color:#f87171}}
.rb-uses{{background:rgba(108,142,247,.15);color:#6c8ef7}}
.rb-supports{{background:rgba(251,191,36,.15);color:#fbbf24}}
.rb-mentions{{background:rgba(148,163,184,.1);color:var(--mt)}}
.ideas{{display:flex;flex-direction:column;gap:8px}}
.idea{{background:rgba(0,0,0,.2);border-left:3px solid rgba(108,142,247,.4);
  border-radius:0 5px 5px 0;padding:8px 12px}}
.idea-n{{color:#a78bfa;font-weight:700;margin-right:6px}}
.idea strong{{font-size:12px;color:var(--tx)}}
.idea p{{font-size:11px;color:#b0bec5;margin-top:4px;line-height:1.5}}
.btn{{background:var(--sf2);border:1px solid var(--br);color:var(--mt);
  padding:5px 12px;border-radius:6px;cursor:pointer;font-size:11px;margin-right:6px}}
.btn:hover{{color:var(--tx)}}
</style></head><body><div class="wrap">
<h1>📊 H vs I-a Comparison — Fair Test v3</h1>
<div class="sub">Both schemes: paper-analyst agent · wq/minimaxm25 · same SQL source · {today}</div>

<div class="stats">
  <div class="st">
    <div class="sv" style="color:{'#4ade80' if avg_h>=80 else '#facc15' if avg_h>=50 else '#f87171'}">{avg_h}%</div>
    <div class="sl">Scheme H avg<br>pre-embedded list</div>
  </div>
  <div class="st">
    <div class="sv" style="color:{'#4ade80' if avg_ia>=80 else '#facc15' if avg_ia>=50 else '#f87171'}">{avg_ia}%</div>
    <div class="sl">Scheme I-a avg<br>agent self-query</div>
  </div>
  <div class="st">
    <div class="sv" style="color:{'#4ade80' if avg_ia>=avg_h else '#f87171'}">{'+' if avg_ia>=avg_h else ''}{diff}%</div>
    <div class="sl">I-a vs H</div>
  </div>
</div>

<div class="note-box">
  <strong>数据源一致性保证：</strong> H 的参考列表和 I-a 的查询命令来自同一条 SQL：<br>
  <code style="font-family:monospace;font-size:11px;color:#6c8ef7">
    SELECT p.title, p.id FROM paper_edges e JOIN papers p ON e.dst_id=p.id
    WHERE e.src_id='{{arxiv_id}}' AND e.edge_type='CITES' ORDER BY p.title
  </code><br>
  <strong>唯一变量：</strong> H = 列表预嵌入 prompt；I-a = agent 在 session 中执行命令后再生成。
</div>

<div style="margin-bottom:14px">
  <button class="btn" onclick="document.querySelectorAll('[id^=b-]').forEach(e=>e.style.display='block');document.querySelectorAll('.ei').forEach(e=>e.classList.add('open'))">展开全部</button>
  <button class="btn" onclick="document.querySelectorAll('[id^=b-]').forEach(e=>e.style.display='none');document.querySelectorAll('.ei').forEach(e=>e.classList.remove('open'))">收起全部</button>
</div>

{cards}
</div>
<script>
function toggle(id){{
  const b=document.getElementById('b-'+id),e=document.querySelector('#c-'+id+' .ei');
  const o=b.style.display!=='none';
  b.style.display=o?'none':'block';
  e.classList.toggle('open',!o);
}}
</script>
</body></html>"""

    REPORT_OUT.write_text(html, encoding="utf-8")
    print(f"Report: {REPORT_OUT}")

# ─── Entry ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--step", choices=["build","verify","report","all"], default="build")
    args = p.parse_args()

    if args.step in ("build","all"):  step_build()
    if args.step in ("verify","all"): step_verify()
    if args.step in ("report","all"): step_report()
