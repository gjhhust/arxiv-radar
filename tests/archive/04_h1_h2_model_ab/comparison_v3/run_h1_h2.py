#!/usr/bin/env python3
"""
arxiv-radar H1 vs H2 Comparison

Compares two improved prompt templates:
  H1 (清单型): 3-field method_variants (base_method/variant_tag/description), A/B/C checklist idea
  H2 (判断型): 2-field method_variants (variant_tag/description), solid-检验 idea

Metrics:
  1. cite_accuracy : core_cite titles match reference list (S2 anchor)
  2. method_variants quality: no paper-own S/B/L variants, valid base_method
  3. idea quality: not restating paper contributions (heuristic)

Usage:
  python3 run_h1_h2.py --step verify
  python3 run_h1_h2.py --step report
"""

import json, re, sqlite3, argparse
from pathlib import Path
from difflib import SequenceMatcher
from datetime import datetime
from typing import Optional

# ─── Paths ──────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
RADAR_DIR  = SCRIPT_DIR.parent.parent.parent
DB_PATH    = RADAR_DIR / "data/paper_network.db"
STATE_FILE = SCRIPT_DIR / "state_h1_h2.json"
REPORT_OUT = SCRIPT_DIR / "report_h1_h2.html"

ARXIV_IDS = [
    "2406.07550","2501.07730","2503.08685","2503.10772","2504.08736",
    "2505.21473","2505.12053","2506.05289","2507.08441","2511.20565","2601.01535"
]
SIM_PASS = 0.8

# ─── DB helpers ──────────────────────────────────────────────────────────────
SQL_REFS = (
    "SELECT p.title, p.id FROM paper_edges e "
    "JOIN papers p ON e.dst_id = p.id "
    "WHERE e.src_id = '{arxiv_id}' AND e.edge_type = 'CITES' "
    "ORDER BY p.title"
)

def get_refs(arxiv_id):
    sql = SQL_REFS.replace("{arxiv_id}", arxiv_id)
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(sql)
        return [{"title": r[0] or "", "id": r[1] or ""} for r in cur.fetchall()]
    finally:
        conn.close()

def get_meta(arxiv_id):
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("SELECT title, abstract FROM papers WHERE id = ?", (arxiv_id,))
        row = cur.fetchone()
        return {"title": row[0] or "", "abstract": row[1] or ""} if row else {}
    finally:
        conn.close()

# ─── Similarity ──────────────────────────────────────────────────────────────
def sim(a, b):
    a2 = re.sub(r'[^\w\s]', ' ', a.lower())
    b2 = re.sub(r'[^\w\s]', ' ', b.lower())
    return SequenceMatcher(None, a2, b2).ratio()

# ─── Cite verification ───────────────────────────────────────────────────────
def verify_cites(data, refs):
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
        "_cite_sims":   sims,
        "_cite_matched": matched,
        "_cite_total":   total,
        "_cite_rate":    round(matched / max(total, 1), 3),
        "_ref_count":    len(ref_titles),
    }

# ─── method_variants analysis ────────────────────────────────────────────────
# Flags: paper-own model size variants (TiTok-S/B/L, Small/Base/Large patterns)
SIZE_PAT = re.compile(
    r'\b(small|base|large|tiny|huge|giant|xl|xxl|s\b|b\b|l\b|h\b|'
    r'\d+[bm]|\d+m?\s*param|nano|micro|medium|mini)\b',
    re.IGNORECASE
)
PLACEHOLDER_PAT = re.compile(r'^base_method:', re.IGNORECASE)

def analyze_method_variants(data, arxiv_id):
    variants = data.get("method_variants", [])
    issues = []
    for v in variants:
        tag = v.get("variant_tag", "") or v.get("base_method", "")
        desc = v.get("description", "")
        # Check if tag contains paper-own size labels
        if SIZE_PAT.search(tag):
            issues.append({"type": "size_variant", "tag": tag,
                            "note": "Appears to be paper's own model size variant"})
        # Check for unfilled placeholder
        if PLACEHOLDER_PAT.match(tag):
            issues.append({"type": "placeholder", "tag": tag,
                            "note": "base_method placeholder not substituted"})
        # Check if description is suspiciously short
        if len(desc) < 20:
            issues.append({"type": "empty_desc", "tag": tag,
                            "note": "Description too short"})
    return {
        "_mv_count":  len(variants),
        "_mv_issues": issues,
        "_mv_ok":     len(issues) == 0,
    }

# ─── idea quality analysis ───────────────────────────────────────────────────
# Heuristic: if idea.why starts with "本文已经" / "论文提出" / "作者提出" → restate
RESTATE_PAT = re.compile(
    r'(本文已|论文(已|提出|实现|证明)|作者(提出|实现)|这篇论文|该论文|paper propose|'
    r'the paper|the authors propose|we propose|already|已经.*实现)',
    re.IGNORECASE
)
# Good idea: mentions gap word
GAP_PAT = re.compile(
    r'(尚未|没有解决|局限|不足|gap|limitation|未探索|remains unexplored|'
    r'lacks|缺乏|忽略了|ignored|could be|potential|hypothesis|假设)',
    re.IGNORECASE
)

def analyze_ideas(data):
    ideas = data.get("idea", [])
    analysis = []
    for i, idea in enumerate(ideas):
        why = idea.get("why", "")
        title = idea.get("title", "")
        restate_flag = bool(RESTATE_PAT.search(why))
        gap_flag     = bool(GAP_PAT.search(why))
        analysis.append({
            "title":   title,
            "why_len": len(why),
            "has_gap": gap_flag,
            "restate": restate_flag,
        })
    restate_count = sum(1 for a in analysis if a["restate"])
    gap_count     = sum(1 for a in analysis if a["has_gap"])
    return {
        "_idea_count":   len(ideas),
        "_idea_analysis": analysis,
        "_idea_restate": restate_count,
        "_idea_gap":     gap_count,
        "_idea_quality": round((gap_count - restate_count * 0.5) / max(len(ideas), 1), 3),
    }

# ─── find result ─────────────────────────────────────────────────────────────
def find_result(arxiv_id, scheme):
    suffix = "results_20260314_{}.json".format(scheme)
    roots = [
        Path.home() / ".openclaw/workspace-paper-analyst/papers",
        Path.home() / ".openclaw/workspace-paper-analyst/papers",
    ]
    for root in roots:
        # New path: analyse-results/ subfolder
        p = root / arxiv_id / "analyse-results" / suffix
        if p.exists(): return p
        # Legacy path: flat in papers/{id}/
        p = root / arxiv_id / suffix
        if p.exists(): return p
    return None


def repair_json(raw):
    """Best-effort repair of malformed JSON from agent output."""
    # Fix: {title": → {"title":
    fixed = raw.replace('{title":', '{"title":')
    try:
        return json.loads(fixed)
    except Exception:
        pass
    # Fix: extra trailing data
    try:
        obj, _ = json.JSONDecoder().raw_decode(fixed)
        return obj
    except Exception as e:
        raise ValueError("Cannot repair: {}".format(e))

# ─── Step: verify ────────────────────────────────────────────────────────────
RETRY_THRESHOLD = 0.6

def step_verify():
    state = {}
    retry_list = []
    for aid in ARXIV_IDS:
        refs = get_refs(aid)
        state[aid] = {}
        for scheme in ["H1", "H2"]:
            path = find_result(aid, scheme)
            if not path:
                print("[MISSING] {} {}".format(aid, scheme))
                retry_list.append({"arxiv_id": aid, "scheme": scheme, "reason": "missing"})
                continue
            try:
                data = json.loads(path.read_text())
            except Exception:
                # Try repair
                try:
                    data = repair_json(path.read_text())
                    print("[REPAIRED] {} {} JSON".format(aid, scheme))
                except Exception as e:
                    print("[ERR] {} {}: {}".format(aid, scheme, e))
                    retry_list.append({"arxiv_id": aid, "scheme": scheme, "reason": "json_error"})
                    continue

            cv = verify_cites(data, refs)
            mv = analyze_method_variants(data, aid)
            iv = analyze_ideas(data)
            data.update(cv)
            data.update(mv)
            data.update(iv)
            data["_verified_by"] = "Mox"
            data["_verified_at"] = datetime.now().strftime("%Y-%m-%d")

            # Write back verification to source file (in-place)
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

            state[aid][scheme] = {
                "cite_matched": cv["_cite_matched"],
                "cite_total":   cv["_cite_total"],
                "cite_rate":    cv["_cite_rate"],
                "mv_count":     mv["_mv_count"],
                "mv_ok":        mv["_mv_ok"],
                "mv_issues":    len(mv["_mv_issues"]),
                "idea_restate": iv["_idea_restate"],
                "idea_gap":     iv["_idea_gap"],
                "idea_quality": iv["_idea_quality"],
                "path":         str(path),
            }
            tag = "[OK]"
            if cv["_cite_rate"] < RETRY_THRESHOLD:
                tag = "[LOW]"
                retry_list.append({"arxiv_id": aid, "scheme": scheme,
                                   "reason": "cite_rate={:.0f}%".format(cv["_cite_rate"]*100)})
            print("{} {} {} | cite:{}/{} ({:.0f}%) | mv:{}(issues:{}) | idea_gap:{}/restate:{}".format(
                tag, aid, scheme,
                cv["_cite_matched"], cv["_cite_total"], cv["_cite_rate"] * 100,
                mv["_mv_count"], len(mv["_mv_issues"]),
                iv["_idea_gap"], iv["_idea_restate"]
            ))

    STATE_FILE.write_text(json.dumps({"results": state, "retry": retry_list},
                                     indent=2, ensure_ascii=False))
    print("\nState saved: {}".format(STATE_FILE))

    for scheme in ["H1", "H2"]:
        rs = [state[a][scheme] for a in ARXIV_IDS if scheme in state.get(a, {})]
        if rs:
            avg_cite = round(sum(r["cite_rate"] for r in rs) / len(rs) * 100, 1)
            avg_mv_ok = round(sum(1 for r in rs if r["mv_ok"]) / len(rs) * 100, 1)
            avg_gap   = round(sum(r["idea_gap"] for r in rs) / len(rs), 2)
            avg_rest  = round(sum(r["idea_restate"] for r in rs) / len(rs), 2)
            print("  {} | cite_avg:{:.1f}% | mv_ok:{:.0f}% | idea_gap:{:.1f} idea_restate:{:.1f}".format(
                scheme, avg_cite, avg_mv_ok, avg_gap, avg_rest
            ))

    if retry_list:
        print("\n⚠️  Retry needed ({} items, cite < {:.0f}%):".format(
            len(retry_list), RETRY_THRESHOLD * 100))
        for r in retry_list:
            print("  {} {} — {}".format(r["arxiv_id"], r["scheme"], r["reason"]))

# ─── Step: report ────────────────────────────────────────────────────────────
def step_report():
    if not STATE_FILE.exists():
        print("[ERR] Run --step verify first"); return
    state = json.loads(STATE_FILE.read_text()).get("results", {})

    def load_result(aid, scheme):
        s = state.get(aid, {}).get(scheme, {})
        if not s: return None
        try: return json.loads(Path(s["path"]).read_text())
        except: return None

    def esc(s):
        return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"','&quot;')

    def cite_bar(matched, total):
        pct = int(matched / max(total, 1) * 100)
        color = "#22c55e" if pct >= 90 else "#f59e0b" if pct >= 70 else "#ef4444"
        return '<span style="color:{};font-weight:bold">{}/{} ({}%)</span>'.format(
            color, matched, total, pct)

    def mv_badge(mv_ok, issues):
        if mv_ok:
            return '<span style="background:#22c55e;color:#fff;padding:1px 6px;border-radius:4px;font-size:11px">OK</span>'
        return '<span style="background:#ef4444;color:#fff;padding:1px 6px;border-radius:4px;font-size:11px">{} issue(s)</span>'.format(issues)

    def idea_badge(gap, restate):
        color = "#22c55e" if restate == 0 and gap >= 2 else "#f59e0b" if restate <= 1 else "#ef4444"
        return '<span style="color:{};font-weight:bold">gap:{} / restate:{}</span>'.format(
            color, gap, restate)

    rows = []
    for aid in ARXIV_IDS:
        meta = get_meta(aid)
        title = meta.get("title", aid)
        d1 = load_result(aid, "H1")
        d2 = load_result(aid, "H2")
        s1 = state.get(aid, {}).get("H1", {})
        s2 = state.get(aid, {}).get("H2", {})

        def field(d, key, fallback="—"):
            if d is None: return fallback
            v = d.get(key)
            if v is None: return fallback
            return v

        # method_variants rendering
        def render_mv(d):
            if d is None: return "<em>missing</em>"
            mvs = d.get("method_variants", [])
            if not mvs: return "<em>[] (none)</em>"
            parts = []
            for v in mvs:
                tag = v.get("variant_tag") or v.get("base_method","?")
                bm  = v.get("base_method","")
                desc= v.get("description","")[:120]
                parts.append("<b>{}</b>{}<br><small>{}</small>".format(
                    esc(tag),
                    " <em>(base: {})</em>".format(esc(bm)) if bm and bm not in tag else "",
                    esc(desc)
                ))
            return "<br><br>".join(parts)

        def render_ideas(d):
            if d is None: return "<em>missing</em>"
            ideas = d.get("idea", [])
            parts = []
            for i, idea in enumerate(ideas):
                t = idea.get("title","")
                w = idea.get("why","")
                src = ["A","B","C"][i] if i < 3 else str(i+1)
                parts.append("<b>[{}] {}</b><br><small>{}</small>".format(
                    src, esc(t), esc(w[:200])
                ))
            return "<br><br>".join(parts) if parts else "<em>empty</em>"

        def render_mv_issues(d):
            if d is None: return ""
            issues = d.get("_mv_issues", [])
            if not issues: return ""
            return "<ul style='margin:2px 0;padding-left:14px;color:#ef4444;font-size:11px'>" + \
                   "".join("<li>{}: {}</li>".format(esc(i["type"]),esc(i["tag"])) for i in issues) + \
                   "</ul>"

        cite1 = cite_bar(s1.get("cite_matched",0), s1.get("cite_total",0)) if s1 else "—"
        cite2 = cite_bar(s2.get("cite_matched",0), s2.get("cite_total",0)) if s2 else "—"
        mv1b  = mv_badge(s1.get("mv_ok",False), s1.get("mv_issues",0)) if s1 else "—"
        mv2b  = mv_badge(s2.get("mv_ok",False), s2.get("mv_issues",0)) if s2 else "—"
        id1b  = idea_badge(s1.get("idea_gap",0), s1.get("idea_restate",0)) if s1 else "—"
        id2b  = idea_badge(s2.get("idea_gap",0), s2.get("idea_restate",0)) if s2 else "—"

        rows.append("""
<tr>
  <td colspan="3" style="background:#1e293b;color:#94a3b8;font-size:11px;padding:6px 8px">
    <b style="color:#e2e8f0">{aid}</b> — {title}
  </td>
</tr>
<tr>
  <td style="vertical-align:top;padding:8px;width:100px">
    <b style="color:#60a5fa">Cite</b><br>H1: {cite1}<br>H2: {cite2}
    <br><br><b style="color:#a78bfa">MV</b><br>H1: {mv1b}{mv1_issues}<br>H2: {mv2b}{mv2_issues}
    <br><br><b style="color:#34d399">Idea</b><br>H1: {id1b}<br>H2: {id2b}
  </td>
  <td style="vertical-align:top;padding:8px;border-left:1px solid #334155">
    <div style="font-size:11px;color:#7dd3fc;margin-bottom:4px">H1 method_variants</div>
    <div style="font-size:12px">{mv1}</div>
    <div style="font-size:11px;color:#7dd3fc;margin:8px 0 4px">H1 idea</div>
    <div style="font-size:12px">{id1}</div>
  </td>
  <td style="vertical-align:top;padding:8px;border-left:1px solid #334155">
    <div style="font-size:11px;color:#86efac;margin-bottom:4px">H2 method_variants</div>
    <div style="font-size:12px">{mv2}</div>
    <div style="font-size:11px;color:#86efac;margin:8px 0 4px">H2 idea</div>
    <div style="font-size:12px">{id2}</div>
  </td>
</tr>""".format(
            aid=esc(aid), title=esc(title[:80]),
            cite1=cite1, cite2=cite2,
            mv1b=mv1b, mv2b=mv2b,
            mv1_issues=render_mv_issues(d1),
            mv2_issues=render_mv_issues(d2),
            id1b=id1b, id2b=id2b,
            mv1=render_mv(d1), mv2=render_mv(d2),
            id1=render_ideas(d1), id2=render_ideas(d2),
        ))

    # Summary stats
    h1_papers = [state[a]["H1"] for a in ARXIV_IDS if "H1" in state.get(a,{})]
    h2_papers = [state[a]["H2"] for a in ARXIV_IDS if "H2" in state.get(a,{})]

    def avg_stat(papers, key):
        vals = [p[key] for p in papers if key in p]
        return round(sum(vals)/len(vals), 3) if vals else 0.0

    h1_cite  = round(avg_stat(h1_papers, "cite_rate") * 100, 1)
    h2_cite  = round(avg_stat(h2_papers, "cite_rate") * 100, 1)
    h1_mv_ok = round(sum(1 for p in h1_papers if p.get("mv_ok")) / max(len(h1_papers),1) * 100, 1)
    h2_mv_ok = round(sum(1 for p in h2_papers if p.get("mv_ok")) / max(len(h2_papers),1) * 100, 1)
    h1_gap   = round(avg_stat(h1_papers, "idea_gap"), 2)
    h2_gap   = round(avg_stat(h2_papers, "idea_gap"), 2)
    h1_rest  = round(avg_stat(h1_papers, "idea_restate"), 2)
    h2_rest  = round(avg_stat(h2_papers, "idea_restate"), 2)

    html = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="UTF-8">
<title>H1 vs H2 Comparison — arxiv-radar</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0f172a; color: #e2e8f0; font-family: system-ui,sans-serif; padding: 24px; }}
  h1 {{ font-size: 20px; margin-bottom: 8px; color: #f8fafc; }}
  .meta {{ color: #64748b; font-size: 12px; margin-bottom: 20px; }}
  .summary {{ display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }}
  .card {{ background: #1e293b; border-radius: 8px; padding: 16px; min-width: 180px; }}
  .card h3 {{ font-size: 12px; color: #64748b; text-transform: uppercase; margin-bottom: 8px; }}
  .card .val {{ font-size: 24px; font-weight: bold; }}
  .card .sub {{ font-size: 12px; color: #94a3b8; margin-top: 4px; }}
  .green {{ color: #22c55e; }} .amber {{ color: #f59e0b; }} .red {{ color: #ef4444; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  tr:nth-child(4n+1) td {{ background: #0f172a; }}
  tr:nth-child(4n+3) td {{ background: #111827; }}
  td {{ padding: 8px; vertical-align: top; }}
  td:first-child {{ width: 160px; }}
  td:nth-child(2), td:nth-child(3) {{ width: calc(50% - 80px); }}
  em {{ color: #475569; }}
  small {{ color: #94a3b8; }}
</style>
</head><body>
<h1>H1 vs H2 Prompt Comparison</h1>
<div class="meta">Generated: {ts} | 11 papers × 2 schemes = 22 sessions | Cite threshold: {thresh}</div>

<div class="summary">
  <div class="card">
    <h3>Cite Accuracy</h3>
    <div class="val {c1col}">{h1_cite}%</div>
    <div class="sub">H1 (清单型) avg</div>
    <div class="val {c2col}" style="margin-top:8px">{h2_cite}%</div>
    <div class="sub">H2 (判断型) avg</div>
  </div>
  <div class="card">
    <h3>method_variants OK</h3>
    <div class="val {mv1col}">{h1_mv_ok}%</div>
    <div class="sub">H1: no size/placeholder issues</div>
    <div class="val {mv2col}" style="margin-top:8px">{h2_mv_ok}%</div>
    <div class="sub">H2: no size/placeholder issues</div>
  </div>
  <div class="card">
    <h3>Idea: gap mentions</h3>
    <div class="val {ig1col}">{h1_gap}</div>
    <div class="sub">H1 avg / 3 ideas</div>
    <div class="val {ig2col}" style="margin-top:8px">{h2_gap}</div>
    <div class="sub">H2 avg / 3 ideas</div>
  </div>
  <div class="card">
    <h3>Idea: restate count</h3>
    <div class="val {ir1col}">{h1_rest}</div>
    <div class="sub">H1 avg (lower=better)</div>
    <div class="val {ir2col}" style="margin-top:8px">{h2_rest}</div>
    <div class="sub">H2 avg (lower=better)</div>
  </div>
</div>

<table>
<thead>
  <tr style="background:#1e293b">
    <th style="padding:8px;text-align:left">Metrics</th>
    <th style="padding:8px;text-align:left;color:#60a5fa">H1 (清单型)</th>
    <th style="padding:8px;text-align:left;color:#86efac">H2 (判断型)</th>
  </tr>
</thead>
<tbody>
{rows}
</tbody>
</table>
</body></html>""".format(
        ts=datetime.now().strftime("%Y-%m-%d %H:%M"),
        thresh=SIM_PASS,
        h1_cite=h1_cite, h2_cite=h2_cite,
        c1col="green" if h1_cite >= 90 else "amber" if h1_cite >= 70 else "red",
        c2col="green" if h2_cite >= 90 else "amber" if h2_cite >= 70 else "red",
        h1_mv_ok=h1_mv_ok, h2_mv_ok=h2_mv_ok,
        mv1col="green" if h1_mv_ok >= 80 else "amber",
        mv2col="green" if h2_mv_ok >= 80 else "amber",
        h1_gap=h1_gap, h2_gap=h2_gap,
        ig1col="green" if h1_gap >= 2.0 else "amber",
        ig2col="green" if h2_gap >= 2.0 else "amber",
        h1_rest=h1_rest, h2_rest=h2_rest,
        ir1col="green" if h1_rest == 0 else "amber" if h1_rest <= 1 else "red",
        ir2col="green" if h2_rest == 0 else "amber" if h2_rest <= 1 else "red",
        rows="\n".join(rows)
    )

    REPORT_OUT.write_text(html, encoding="utf-8")
    print("Report: {}".format(REPORT_OUT))
    print("\nSummary:")
    print("  H1 cite:{:.1f}%  mv_ok:{:.0f}%  idea_gap:{:.2f}  idea_restate:{:.2f}".format(
        h1_cite, h1_mv_ok, h1_gap, h1_rest))
    print("  H2 cite:{:.1f}%  mv_ok:{:.0f}%  idea_gap:{:.2f}  idea_restate:{:.2f}".format(
        h2_cite, h2_mv_ok, h2_gap, h2_rest))

# ─── main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", choices=["verify","report"], required=True)
    args = parser.parse_args()
    if args.step == "verify": step_verify()
    elif args.step == "report": step_report()
