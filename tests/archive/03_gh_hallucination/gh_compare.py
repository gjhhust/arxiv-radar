"""
gh_compare.py v3 — Scheme G vs H hallucination test
  Verification: DB per-paper reference lookup (no bib dependency)
    SELECT p.id, p.title FROM papers p
    JOIN paper_edges pe ON pe.dst_id = p.id
    WHERE pe.src_id = '{paper_id}' AND pe.edge_type = 'CITES'
  Threshold: Jaccard > 0.5 OR word_count <= 3 → suspicious

Flow:
  G×11  → DB verify (Round1 率) → 幻觉篇带警告重跑 (Round2 率)
  H×11  → DB verify (no re-run)
  HTML: 3列 G | G2 | H，每条cite显示分数，末尾F方案回溯对比
"""
from __future__ import annotations
import gzip, json, logging, os, re, shutil, sqlite3, sys, tarfile, time, urllib.request
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SKILL_DIR   = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

TEST_DIR  = Path(__file__).parent
WORKSPACE = Path("/tmp/pa_gh")
STATE_F   = TEST_DIR / "state_gh.json"
DB_PATH   = SKILL_DIR / "data" / "paper_network.db"

PAPERS = ["2406.07550","2501.07730","2503.08685","2503.10772","2504.08736",
          "2505.12053","2505.21473","2506.05289","2507.08441","2511.20565","2601.01535"]
MODEL = "minimaxm25"

# ── DB reference lookup ───────────────────────────────────────────────

def get_paper_refs_from_db(arxiv_id: str) -> list[tuple[str, str]]:
    """Returns list of (ref_arxiv_id, ref_title) for a paper's CITES edges."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute("""
            SELECT p.id, p.title FROM papers p
            JOIN paper_edges pe ON pe.dst_id = p.id
            WHERE pe.src_id = ? AND pe.edge_type = 'CITES'
        """, (arxiv_id,)).fetchall()
        conn.close()
        return [(r[0], r[1]) for r in rows if r[1]]
    except Exception as e:
        logger.warning(f"DB lookup failed for {arxiv_id}: {e}")
        return []

# ── Verification (Jaccard, per-paper DB refs) ─────────────────────────

def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a | b) else 0.0

def _norm_words(s: str) -> set:
    return set(w for w in re.sub(r"[^a-z0-9\s]", " ", s.lower()).split() if len(w) > 2)

def verify_one(title: str, ref_titles: list[str]) -> dict:
    """Returns {suspicious, sim, matched}"""
    words = title.strip().split()
    if len(words) <= 3:
        return {"suspicious": True, "sim": 0.0, "source": "word_count", "matched": ""}
    t_w = _norm_words(title)
    best_sim, best_title = 0.0, ""
    for rt in ref_titles:
        s = _jaccard(t_w, _norm_words(rt))
        if s > best_sim:
            best_sim, best_title = s, rt
    return {
        "suspicious": best_sim <= 0.5,
        "sim": round(best_sim, 3),
        "source": "db",
        "matched": best_title[:80],
    }

def verify_core_cite(arxiv_id: str, core_cite: list[dict]) -> dict:
    """Verify core_cite titles against this paper's DB references."""
    ref_titles = [t for _, t in get_paper_refs_from_db(arxiv_id)]
    result = {}
    for entry in core_cite:
        title = entry.get("title", "").strip()
        if title:
            result[title] = verify_one(title, ref_titles)
    return result

# ── Download & Parse (for LaTeX text + meta) ─────────────────────────

def download_and_parse(arxiv_id: str) -> tuple[dict, str]:
    """Returns (meta_dict, paper_text). Uses DB for title/abstract."""
    paper_dir = WORKSPACE / arxiv_id
    if paper_dir.exists(): shutil.rmtree(paper_dir)
    paper_dir.mkdir(parents=True, exist_ok=True)

    # Get meta from DB (already populated by build_test_db.py)
    meta = {"id": arxiv_id, "arxiv_id": arxiv_id, "title": arxiv_id, "abstract": ""}
    try:
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute("SELECT title, abstract FROM papers WHERE id = ?",
                           (arxiv_id,)).fetchone()
        conn.close()
        if row:
            meta.update({"title": row[0] or arxiv_id, "abstract": row[1] or ""})
    except: pass

    # Download LaTeX source
    paper_text = ""
    try:
        req = urllib.request.Request(f"https://arxiv.org/e-print/{arxiv_id}",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=90) as r: raw = r.read()
        tarball = paper_dir / "src.tar.gz"
        tarball.write_bytes(raw)
        src = paper_dir / "src"; src.mkdir(exist_ok=True)
        try:
            with tarfile.open(tarball, "r:gz") as tf: tf.extractall(src)
        except:
            try:
                with tarfile.open(tarball, "r:*") as tf: tf.extractall(src)
            except:
                try: (src / "main.tex").write_bytes(gzip.decompress(raw))
                except: pass
        total = 0
        for tf in sorted(src.rglob("*.tex")):
            if total >= 50000: break
            try:
                c = tf.read_text(errors="replace")
                if len(c) < 300: continue
                paper_text += f"\n\n=== {tf.name} ===\n" + c[:15000]
                total += len(c)
            except: pass
        logger.info(f"  [{arxiv_id}] text={len(paper_text)} | title={meta['title'][:50]}")
    except Exception as e:
        logger.warning(f"  [{arxiv_id}] download error: {e}")
    return meta, paper_text

# ── Prompts ───────────────────────────────────────────────────────────

PROMPT_BASE = """\
你是 CV 领域资深研究员，每周阅读 20+ 篇论文。分析给定论文，输出结构化 JSON。

分析要求：

cn_oneliner（≤45字）
格式：「基于[X]引入[Y]实现[Z]」或「把[A]和[B]结合解决[C]」
必须包含：具体的基础方法名 + 具体改动 + 具体效果。不要泛泛的"改进"。

cn_abstract（2-4句中文技术摘要）
完整。关键术语保留英文。必须完整，不得截断。

contribution_type（严格四选一）
- incremental: 在已有方法上做了有效但可预期的改进
- significant: 解决了领域内已知的难题，或提供了其他人可复用的新方法/框架
- story-heavy: 工程堆砌为主，叙事高于实质
- foundational: 改变了领域做事方式，未来方法会以此为起点
从严判断，多数论文是 incremental。

editorial_note（必须按三段结构写，总字数 80-150 字）
[前驱] 这篇论文建立在哪些已有工作的基础上，核心模块各来自哪里。
[贡献] 去掉包装之后，作者真正做了什么新事情（用最简单的话）。
[判断] 这个贡献的实质价值：是真正解决了问题，还是有效但不深刻，或夸大了贡献。

why_read（1句，自由但要有判断力）
说清楚谁值得读，具体会从中得到什么。不要"如果你做这个领域可以看看"这种废话。

method_variants（方法变体列表）
- base_method: 具体已有方法名（小写，如 vqgan, titok, magvit-v2）
- variant_tag: base_method:改动点（中划线连接，全小写，如 vqgan:hybrid-encoder）
- description: 原{base_method}做法是X；本文改为Y，目的Z

core_cite（强制 ≥10 条，按重要性排序）
权重排序：
1. Method 章节直接构建在其上的工作（role=extends，最高权重）
2. 用到其组件/backbone/预训练模型（role=uses）
3. Experiments 中 baseline 对比（role=contrasts）
4. Introduction 中支持动机的引用（role=supports）
5. Related Work 背景引用（role=mentions）
不可省略：所有 contrasts 类 + 所有 extends 类引用。
role 选唯一最准确的值，五选一：extends | contrasts | uses | supports | mentions
禁止组合写法（不得输出"extends/uses"等）。
每条：title=原始英文论文标题 | role | note=与本文的具体关系（1句）"""

PROMPT_G = PROMPT_BASE + "\n\n输出格式：严格 JSON，不要 markdown 代码块，不要任何额外文字：\n" + \
    '{"cn_oneliner":"","cn_abstract":"","contribution_type":"","editorial_note":"","why_read":"","method_variants":[{"base_method":"","variant_tag":"","description":""}],"core_cite":[{"title":"","role":"","note":""}]}'

PROMPT_H = PROMPT_BASE + """

（提交前自查）core_cite 每条 title 须出现在正文参考文献列表或 bib 文件中；如不确定是否真实存在，访问 https://arxiv.org/search/?query=<关键词>&searchtype=all 搜索核查；无法确认的请从列表中移除。

输出格式：严格 JSON，不要 markdown 代码块，不要任何额外文字：
{"cn_oneliner":"","cn_abstract":"","contribution_type":"","editorial_note":"","why_read":"","method_variants":[{"base_method":"","variant_tag":"","description":""}],"core_cite":[{"title":"","role":"","note":""}]}"""

# ── State ─────────────────────────────────────────────────────────────

def load_state(): return json.loads(STATE_F.read_text()) if STATE_F.exists() else {}
def save_state(s): STATE_F.write_text(json.dumps(s, indent=2, ensure_ascii=False))

# ── Run scheme ────────────────────────────────────────────────────────

def run_scheme(scheme: str, res_dir: Path, prompt: str,
               papers=None, correction_for=None):
    from paper_analyst import analyze_paper
    res_dir.mkdir(parents=True, exist_ok=True)

    for pid in (papers or PAPERS):
        out = res_dir / f"{pid}.json"
        if out.exists():
            logger.info(f"  SKIP {pid}")
            continue
        logger.info(f"\n{'='*55}\n  [{scheme}] {pid}\n{'='*55}")
        meta, text = download_and_parse(pid)

        sp = prompt
        if correction_for and pid in correction_for:
            bad_list = "\n".join(f"  - {t}" for t in correction_for[pid])
            sp = prompt + (
                f"\n\n⚠️ 以下标题在上一次输出中经验证不存在于该论文的引用列表，"
                f"本次严禁出现：\n{bad_list}"
            )

        result = analyze_paper(
            meta, {},  # empty bib_mapping — DB is source of truth
            text, model=MODEL, fallback_model=None,
            retry_on_parse_fail=False,
            system_prompt_override=sp,
            hallucination_check=False,
        )
        result.update({"arxiv_id": pid, "scheme": scheme})
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        logger.info(f"  ✓ {result['latency_s']:.1f}s  cites={len(result.get('core_cite', []))}")
        time.sleep(3)

def run_verify(res_dir: Path) -> dict:
    """Verify all results in res_dir using DB per-paper refs. Returns summary dict."""
    summary = {}
    for f in sorted(res_dir.glob("*.json")):
        d = json.loads(f.read_text())
        pid = f.stem
        cc = d.get("core_cite", [])
        vres = verify_core_cite(pid, cc)
        susp = [t for t, v in vres.items() if v["suspicious"]]
        d["verify"] = vres
        d["suspicious_titles"] = susp
        f.write_text(json.dumps(d, indent=2, ensure_ascii=False))
        summary[pid] = {"total": len(cc), "susp_count": len(susp),
                        "suspicious": susp, "verify": vres}
        logger.info(f"  {pid}: {len(susp)}/{len(cc)} suspicious")
    return summary

def print_rate(label: str, summary: dict) -> float:
    tc = sum(v["total"] for v in summary.values())
    ts = sum(v["susp_count"] for v in summary.values())
    r  = ts / tc * 100 if tc else 0
    print(f"\n[{label}] 幻觉率 {ts}/{tc} = {r:.1f}%")
    for pid, v in summary.items():
        if v["susp_count"]:
            print(f"  {pid}: {v['susp_count']}/{v['total']}")
            for t in v["suspicious"][:2]:
                print(f"    ⚠ {t[:65]}")
    return r

# ── HTML Report ───────────────────────────────────────────────────────

def esc(s): return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def generate_report(g1_dir: Path, g2_dir: Path, h_dir: Path, out: Path):
    def load(d):
        if not d or not d.exists(): return {}
        return {f.stem: json.loads(f.read_text()) for f in d.glob("*.json")}

    g1 = load(g1_dir)
    g2 = load(g2_dir)
    h  = load(h_dir)

    # Previous F results from test_e2e
    f_prev = {}
    f_dir = SKILL_DIR / "test_e2e" / "results"
    if f_dir.exists():
        for ff in f_dir.glob("*_minimaxm25.json"):
            pid = ff.stem.replace("_minimaxm25", "")
            f_prev[pid] = json.loads(ff.read_text())

    def rate(res):
        tc = sum(len(d.get("core_cite", [])) for d in res.values())
        ts = sum(len(d.get("suspicious_titles", [])) for d in res.values())
        return ts, tc, f"{ts/tc*100:.1f}%" if tc else "N/A"

    g1s,g1t,g1r = rate(g1)
    g2s,g2t,g2r = rate(g2)
    hs, ht, hr  = rate(h)
    fps,fpt,fpr = rate(f_prev)

    def col(r_str):
        try: v = float(str(r_str).rstrip('%'))
        except: return "var(--muted)"
        return "var(--green)" if v < 5 else ("var(--yellow)" if v < 15 else "var(--red)")

    role_color = {"extends":"#b08ef5","contrasts":"#f59e0b","uses":"#22d3ee",
                  "supports":"#3ecf8e","mentions":"#7a849a"}

    html = ["""<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>G vs H Hallucination Report</title>
<style>
:root{--bg:#0d0f18;--card:#141720;--card2:#1a1d2b;--border:#252840;
  --accent:#6c8ef5;--green:#3ecf8e;--yellow:#f5a623;--red:#e05c5c;
  --text:#dde3f0;--muted:#7a849a;--mono:'JetBrains Mono',monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font:13px/1.65 -apple-system,sans-serif;padding:14px;padding-bottom:40px}
h1{font-size:17px;color:#fff;margin-bottom:3px}
h2{font-size:12px;color:#fff;margin:20px 0 8px;padding:5px 12px;
   background:var(--card2);border-left:3px solid var(--accent);border-radius:0 4px 4px 0}
.meta{color:var(--muted);font-size:11px;margin-bottom:14px}
.rate-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:10px 0}
.rate-card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:10px;text-align:center}
.rate-card .rn{font-size:22px;font-weight:700}
.rate-card .rl{font-size:10px;color:var(--muted);margin-top:2px}
.pb{margin:14px 0}
.pb-hdr{padding:7px 12px;background:var(--card2);border-radius:4px 4px 0 0;
  font-family:var(--mono);font-size:12px;font-weight:700;border-bottom:1px solid var(--border)}
.cols{display:grid;grid-template-columns:repeat(3,1fr);border:1px solid var(--border);
  border-top:none;border-radius:0 0 6px 6px;overflow:hidden}
.col{padding:8px 10px;border-right:1px solid var(--border)}
.col:last-child{border-right:none}
.col-hdr{font-family:var(--mono);font-size:11px;font-weight:700;color:var(--accent);margin-bottom:5px}
.col-rate{font-size:11px;margin-bottom:6px;padding:3px 8px;border-radius:4px;background:#111;font-family:var(--mono)}
.cite{padding:3px 0;font-size:11px;border-bottom:1px solid #1a1d2b;display:flex;align-items:flex-start;gap:4px;flex-wrap:wrap}
.cite:last-child{border-bottom:none}
.role-tag{flex-shrink:0;padding:1px 5px;border-radius:3px;font-size:9px;font-family:var(--mono);font-weight:700}
.cite-title{flex:1;word-break:break-word}
.sim-badge{flex-shrink:0;font-family:var(--mono);font-size:10px;padding:1px 5px;border-radius:3px;background:#1e2235}
.susp{color:var(--red)}
.ok{color:var(--text)}
.prev-section{margin-top:24px;padding:12px;background:var(--card);border:1px solid var(--border);border-radius:8px}
</style></head><body>""",
        '<h1>Scheme G vs H — core_cite 幻觉率对比</h1>',
        f'<div class="meta">11 papers × minimaxm25 · DB per-paper ref 验证(Jaccard>0.5) · {datetime.now().strftime("%Y-%m-%d %H:%M")}</div>',
        '<div class="rate-grid">',
        f'<div class="rate-card"><div class="rn" style="color:{col(g1r)}">{g1r}</div><div class="rl">G Round1<br>{g1s}/{g1t} suspicious</div></div>',
        f'<div class="rate-card"><div class="rn" style="color:{col(g2r)}">{g2r}</div><div class="rl">G Round2 (重跑)<br>{g2s}/{g2t} suspicious</div></div>',
        f'<div class="rate-card"><div class="rn" style="color:{col(hr)}">{hr}</div><div class="rl">H (self-check)<br>{hs}/{ht} suspicious</div></div>',
        f'<div class="rate-card"><div class="rn" style="color:{col(fpr)}">{fpr}</div><div class="rl">F旧(回溯验证，供参考)<br>{fps}/{fpt}</div></div>',
        '</div>',
        '<h2>逐篇详情（G | G重跑 | H）</h2>',
    ]

    for pid in PAPERS:
        rep = (g1.get(pid) or g2.get(pid) or h.get(pid) or {})
        oneliner = rep.get("cn_oneliner", "")
        html.append(f'<div class="pb" id="{pid}">')
        html.append(f'<div class="pb-hdr">{pid}  <span style="font-weight:400;color:var(--muted)">{esc(oneliner[:55])}</span></div>')
        html.append('<div class="cols">')

        for label, src in [("G Round1", g1), ("G Round2", g2), ("H", h)]:
            d   = src.get(pid, {})
            cc  = d.get("core_cite", [])
            vfy = d.get("verify", {})
            susp = set(d.get("suspicious_titles", []))
            sc = len(susp); tc = len(cc)
            r  = f"{sc/tc*100:.0f}%" if tc else "—"
            rc = col(r)
            html.append('<div class="col">')
            html.append(f'<div class="col-hdr">{label}</div>')
            if not cc:
                html.append('<div style="color:var(--muted);font-size:11px">—</div>')
            else:
                html.append(f'<div class="col-rate">幻觉率 <span style="color:{rc};font-weight:700">{r}</span>  {sc}/{tc}</div>')
                for c in cc:
                    t    = c.get("title", "")
                    role = c.get("role", "")
                    v    = vfy.get(t, {})
                    sim  = v.get("sim", -1)
                    src_tag = v.get("source", "")
                    is_s = t in susp
                    icon = "⚠ " if is_s else ""
                    sim_str = f"{sim:.2f}({src_tag})" if sim >= 0 else "?"
                    sim_clr = "var(--red)" if is_s else ("var(--green)" if sim > 0.5 else "var(--yellow)")
                    rt_clr  = role_color.get(role, "#888")
                    html.append(
                        f'<div class="cite">'
                        f'<span class="role-tag" style="background:#1e2235;color:{rt_clr}">{esc(role)}</span>'
                        f'<span class="cite-title {"susp" if is_s else "ok"}">{icon}{esc(t[:72])}</span>'
                        f'<span class="sim-badge" style="color:{sim_clr}">{sim_str}</span>'
                        f'</div>'
                    )
            html.append('</div>')
        html.append('</div></div>')

    # F方案回溯对比
    if f_prev:
        html.append('<div class="prev-section">')
        html.append('<h2 style="margin:0 0 10px">📊 F方案(旧minimaxm25)回溯对比</h2>')
        html.append('<div style="font-size:11px;color:var(--muted);margin-bottom:8px">回溯用该论文DB引用验证，仅供参考（F方案跑时无此验证）</div>')
        for pid in PAPERS:
            d = f_prev.get(pid)
            if not d: continue
            cc = d.get("core_cite", [])
            vfy = verify_core_cite(pid, cc) if cc else {}
            susp_f = [t for t, v in vfy.items() if v["suspicious"]]
            r  = f"{len(susp_f)/len(cc)*100:.0f}%" if cc else "—"
            rc = col(r)
            html.append(
                f'<div style="padding:4px 0;border-bottom:1px solid var(--border);font-size:12px">'
                f'<span style="font-family:var(--mono);color:var(--accent)">{pid}</span>  '
                f'幻觉率 <span style="color:{rc};font-weight:700">{r}</span>  {len(susp_f)}/{len(cc)}  '
                + (f'<span style="color:var(--red)">⚠ {esc(", ".join(susp_f[:2]))}</span>'
                   if susp_f else '<span style="color:var(--green)">✓ clean</span>')
                + '</div>'
            )
        html.append('</div>')

    html.append('</body></html>')
    out.write_text('\n'.join(html))
    logger.info(f"✓ Report: {out}  {out.stat().st_size // 1024}KB")

# ── Main ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--step", choices=["g1","g-verify","g2","h","h-verify","report","all"])
    args = p.parse_args()

    G1_DIR = TEST_DIR / "results_g1"
    G2_DIR = TEST_DIR / "results_g2"
    H_DIR  = TEST_DIR / "results_h"
    RPT    = TEST_DIR / "report_gh.html"

    if args.step == "g1":
        run_scheme("G", G1_DIR, PROMPT_G)

    elif args.step == "g-verify":
        s = run_verify(G1_DIR)
        r = print_rate("G Round1", s)
        hall = {pid: v["suspicious"] for pid, v in s.items() if v["susp_count"]}
        save_state({"g1_rate": r, "g1_hall": hall})

    elif args.step == "g2":
        hall = load_state().get("g1_hall", {})
        if not hall:
            logger.info("  No G1 hallucinations → copy G1 to G2")
            shutil.copytree(G1_DIR, G2_DIR, dirs_exist_ok=True)
        else:
            G2_DIR.mkdir(parents=True, exist_ok=True)
            for f in G1_DIR.glob("*.json"):
                if f.stem not in hall:
                    shutil.copy(f, G2_DIR / f.name)
            run_scheme("G2", G2_DIR, PROMPT_G, papers=list(hall.keys()),
                       correction_for=hall)

    elif args.step == "h":
        run_scheme("H", H_DIR, PROMPT_H)

    elif args.step == "h-verify":
        s = run_verify(H_DIR)
        r = print_rate("H", s)
        st = load_state(); st["h_rate"] = r; save_state(st)

    elif args.step == "report":
        generate_report(G1_DIR, G2_DIR, H_DIR, RPT)

    elif args.step == "all":
        logger.info("=== G/H Full Test ===")

        logger.info("\n--- G Round1 ---")
        run_scheme("G", G1_DIR, PROMPT_G)
        s1 = run_verify(G1_DIR)
        r1 = print_rate("G Round1", s1)
        hall = {pid: v["suspicious"] for pid, v in s1.items() if v["susp_count"]}
        save_state({"g1_rate": r1, "g1_hall": hall})

        logger.info("\n--- G Round2 ---")
        if hall:
            G2_DIR.mkdir(parents=True, exist_ok=True)
            for f in G1_DIR.glob("*.json"):
                if f.stem not in hall:
                    shutil.copy(f, G2_DIR / f.name)
            run_scheme("G2", G2_DIR, PROMPT_G, papers=list(hall.keys()),
                       correction_for=hall)
            s2 = run_verify(G2_DIR)
            print_rate("G Round2", s2)
        else:
            logger.info("  Zero hallucinations in G1 → skip G2, copy G1")
            shutil.copytree(G1_DIR, G2_DIR, dirs_exist_ok=True)

        logger.info("\n--- H ---")
        run_scheme("H", H_DIR, PROMPT_H)
        s3 = run_verify(H_DIR)
        r3 = print_rate("H", s3)
        st = load_state(); st["h_rate"] = r3; save_state(st)

        generate_report(G1_DIR, G2_DIR, H_DIR, RPT)
        logger.info(f"\n✅ Done → {RPT}")

    else:
        p.print_help()
