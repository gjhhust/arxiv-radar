"""
e2e_test.py v2 — End-to-end pipeline: arxiv download → LLM analysis → HTML report

Fixes vs v1:
  1. raw_output 始终保存到结果文件，parse 失败可事后 repair
  2. HTTP 5xx / 连接重置 → 自动 retry（最多 3 次，指数退避）
  3. gpt52 timeout 提升至 420s（上次 connection reset 根因）
  4. parse 失败时立刻 inline repair，不再丢弃输出
  5. 新 paper list（11 篇视觉 tokenizer 方向论文）
  6. 不在 DB 的论文从 arxiv API 自动抓 title/abstract

Usage:
  python3 e2e_test.py --run
  python3 e2e_test.py --status
  python3 e2e_test.py --html
  python3 e2e_test.py --run --model gpt52   # 只跑单模型补跑
"""
from __future__ import annotations
import gzip, io, json, logging, os, re, shutil, sys, tarfile, time, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── 路径 ────────────────────────────────────────────────────────────
SKILL_DIR   = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

WORKSPACE   = Path("/tmp/pa_e2e")
RESULTS_DIR = Path(__file__).parent / "results"
STATE_FILE  = Path(__file__).parent / "state.json"
HTML_OUT    = Path(__file__).parent / "report.html"

# ── Paper list ──────────────────────────────────────────────────────
MODELS = ["claude46", "minimaxm25", "gpt52"]

PAPERS = [
    "2406.07550",   # TiTok — Image is Worth 32 Tokens
    "2501.07730",   # FlexTok
    "2503.10772",   # ?
    "2503.08685",   # ?
    "2504.08736",   # GigaTok
    "2505.21473",   # DetailFlow
    "2505.12053",   # ?
    "2506.05289",   # ?
    "2507.08441",   # VFMTok
    "2511.20565",   # ?
    "2601.01535",   # ReTok
]

# Model-specific timeouts (gpt52 is very slow)
MODEL_TIMEOUT = {
    "claude46":   180,
    "minimaxm25": 180,
    "gpt52":      420,   # was 300, connection reset @ 125s+
}

# ── arxiv metadata fetch ─────────────────────────────────────────────

def fetch_arxiv_meta(arxiv_id: str) -> dict:
    """Fetch title + abstract from arxiv API if not in local DB."""
    try:
        from paper_db import PaperDB
        db  = PaperDB()
        row = db.get_paper(arxiv_id)
        if row and row.get("title"):
            return row
    except Exception:
        pass

    # Fallback: arxiv API
    url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "arxiv-radar/3.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            xml = r.read().decode("utf-8")
        title_m = re.search(r"<title>(.+?)</title>", xml, re.DOTALL)
        abs_m   = re.search(r"<summary>(.+?)</summary>", xml, re.DOTALL)
        title   = title_m.group(1).strip().replace("\n", " ") if title_m else arxiv_id
        abstract = abs_m.group(1).strip().replace("\n", " ") if abs_m else ""
        # skip the feed-level title
        titles = re.findall(r"<title>(.+?)</title>", xml, re.DOTALL)
        if len(titles) >= 2:
            title = titles[1].strip().replace("\n", " ")
        logger.info(f"  [{arxiv_id}] arxiv API: {title[:60]}")
        return {"id": arxiv_id, "arxiv_id": arxiv_id, "title": title, "abstract": abstract}
    except Exception as e:
        logger.warning(f"  [{arxiv_id}] arxiv API failed: {e}")
        return {"id": arxiv_id, "arxiv_id": arxiv_id, "title": arxiv_id, "abstract": ""}


# ── arxiv source download ────────────────────────────────────────────

def download_and_parse(arxiv_id: str) -> tuple[dict, str]:
    """Fresh download from arxiv.org/e-print, parse bib + tex. Returns (bib_mapping, paper_text)."""
    paper_dir = WORKSPACE / arxiv_id
    if paper_dir.exists():
        shutil.rmtree(paper_dir)
    paper_dir.mkdir(parents=True, exist_ok=True)

    bib_mapping: dict = {}
    paper_text:  str  = ""

    url = f"https://arxiv.org/e-print/{arxiv_id}"
    tarball = paper_dir / "source.tar.gz"
    logger.info(f"  [{arxiv_id}] Downloading source...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=90) as resp:
            raw = resp.read()
        tarball.write_bytes(raw)
        logger.info(f"  [{arxiv_id}] {len(raw)//1024}KB downloaded")
    except Exception as e:
        logger.warning(f"  [{arxiv_id}] Source download failed: {e} — will use abstract only")
        return bib_mapping, paper_text

    # Extract
    src_dir = paper_dir / "src"
    src_dir.mkdir(exist_ok=True)
    try:
        with tarfile.open(tarball, "r:gz") as tf:
            tf.extractall(src_dir)
    except Exception:
        try:
            with tarfile.open(tarball, "r:*") as tf:
                tf.extractall(src_dir)
        except Exception:
            try:
                raw = gzip.decompress(tarball.read_bytes())
                (src_dir / "main.tex").write_bytes(raw)
            except Exception as e:
                logger.warning(f"  [{arxiv_id}] Extract failed: {e}")
                return bib_mapping, paper_text

    # Parse .bib
    for bib_path in list(src_dir.rglob("*.bib"))[:2] + list(src_dir.rglob("*.bbl"))[:1]:
        try:
            bib_content = bib_path.read_text(errors="replace")
            for m in re.finditer(r"@\w+\{(\w+),\s*\n(.*?)\n\}", bib_content, re.DOTALL):
                key  = m.group(1)
                body = m.group(2)
                tm   = re.search(r"title\s*=\s*\{(.+?)\}", body)
                am   = re.search(r"arXiv.*?(\d{4}\.\d{4,5})", body)
                title = tm.group(1).replace("{","").replace("}","") if tm else ""
                if title:
                    bib_mapping[key] = {"title": title, "arxiv_id": am.group(1) if am else None}
        except Exception as e:
            logger.warning(f"  [{arxiv_id}] bib error: {e}")

    # Collect .tex sections
    tex_files = sorted(src_dir.rglob("*.tex"))
    section_order = ["intro","related","method","approach","model","experiment","result","conclusion","ablat"]
    priority, others = [], []
    for tf in tex_files:
        name  = tf.stem.lower()
        score = next((i for i, s in enumerate(section_order) if s in name), 99)
        (priority if score < 99 else others).append((score, tf))
    priority.sort(key=lambda x: x[0])
    ordered = [p[1] for p in priority] + [o[1] for o in others]
    # Put main.tex first
    mains = [t for t in tex_files if t.stem.lower() in ("main","paper","manuscript")]
    if mains:
        ordered = mains + [t for t in ordered if t not in mains]

    total_chars = 0
    for tf in ordered:
        if total_chars >= 52000:
            break
        try:
            content = tf.read_text(errors="replace")
            if len(content) < 300:
                continue
            if re.search(r"\\usepackage|\\documentclass|\\newcommand\{\\", content[:500]) and len(content) < 2000:
                continue
            paper_text += f"\n\n=== {tf.name} ===\n" + content[:15000]
            total_chars += len(content)
        except Exception:
            continue

    logger.info(f"  [{arxiv_id}] bib={len(bib_mapping)} text_chars={len(paper_text)}")
    return bib_mapping, paper_text


# ── Robust LLM call with retry ───────────────────────────────────────

BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://localhost:4141")
API_KEY  = os.environ.get("OPENAI_API_KEY", "test")
ANTHROPIC_MODELS = {"claude46","claude45","glm5","katcoder","kimik25","minimaxm21","minimaxm25","glm47"}

def llm_call_with_retry(system: str, user: str, model: str, max_retries: int = 3) -> tuple[str, float]:
    """
    Call LLM with retry on transient errors (5xx, connection reset).
    Uses per-model timeout from MODEL_TIMEOUT.
    """
    model_id  = model.split("/")[-1] if "/" in model else model
    is_anth   = model_id in ANTHROPIC_MODELS
    timeout   = MODEL_TIMEOUT.get(model_id, 300)

    if is_anth:
        url      = BASE_URL.rstrip("/") + "/messages"
        messages = [{"role": "user", "content": f"[System]\n{system}\n\n[Task]\n{user}"}]
        body     = {"model": model_id, "messages": messages, "max_tokens": 8192}
    else:
        url      = BASE_URL.rstrip("/") + "/oai/chat/completions"
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        body     = {"model": model_id, "messages": messages, "temperature": 0.3, "max_tokens": 8192}

    payload = json.dumps(body).encode()
    req     = urllib.request.Request(url, data=payload,
                headers={"Content-Type":"application/json","Authorization":f"Bearer {API_KEY}"})

    last_exc = None
    for attempt in range(max_retries):
        if attempt > 0:
            wait = 2 ** attempt
            logger.info(f"  retry {attempt}/{max_retries-1} in {wait}s...")
            time.sleep(wait)
        try:
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            latency = time.time() - t0
            data = json.loads(raw)
            if is_anth:
                blocks = data.get("content", [])
                text = next((b["text"] for b in blocks if b.get("type")=="text"), None)
                if text is None: raise ValueError(f"No text block: {blocks[:1]}")
                return text.strip(), latency
            return data["choices"][0]["message"]["content"].strip(), latency
        except urllib.error.HTTPError as e:
            last_exc = e
            if e.code >= 500:
                logger.warning(f"  HTTP {e.code} on attempt {attempt+1}, will retry")
                continue
            raise  # 4xx → don't retry
        except (ConnectionError, OSError, TimeoutError) as e:
            last_exc = e
            logger.warning(f"  Connection error on attempt {attempt+1}: {e}, will retry")
            continue
    raise RuntimeError(f"All {max_retries} attempts failed. Last: {last_exc}")


# ── JSON parse (multi-pass robust) ───────────────────────────────────

def robust_parse(text: str) -> tuple[dict | None, str | None]:
    def try_parse(s):
        try: return json.loads(s)
        except: return None

    text = text.strip()
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if m: text = m.group(1).strip()

    def all_passes(s):
        # Pass 1: direct
        r = try_parse(s)
        if r: return r
        # Pass 2: curly/smart quotes → straight
        s2 = s
        for bad, good in [('\u201c','"'),('\u201d','"'),('\u2018',"'"),('\u2019',"'"),('\u300a',''),('\u300b','')]:
            s2 = s2.replace(bad, good)
        r = try_parse(s2)
        if r: return r
        # Pass 3: drop inner CJK-surrounded quotes
        s3 = re.sub(r'(?<=[\u4e00-\u9fff\w\s])"(?=[\u4e00-\u9fff\w\s])', '', s2)
        r = try_parse(s3)
        if r: return r
        # Pass 4: smart inner-quote stripper
        buf, in_str, esc = [], False, False
        for i, ch in enumerate(s2):
            if esc: buf.append(ch); esc = False
            elif ch == '\\': buf.append(ch); esc = True
            elif ch == '"':
                if not in_str: in_str = True; buf.append(ch)
                else:
                    rest = s2[i+1:i+20].lstrip()
                    if rest and rest[0] in ',:}]': in_str = False; buf.append(ch)
                    # else: drop inner quote silently
            else: buf.append(ch)
        s4 = ''.join(buf)
        r = try_parse(s4)
        if r: return r
        return None

    parsed = all_passes(text)
    if parsed: return parsed, None
    s_idx, e_idx = text.find("{"), text.rfind("}")
    if s_idx >= 0 and e_idx > s_idx:
        parsed = all_passes(text[s_idx:e_idx+1])
        if parsed: return parsed, None
        return None, f"All parse passes failed ({len(text)} chars)"
    return None, f"No JSON brackets found"


# ── Core analysis ─────────────────────────────────────────────────────

def run_cell(arxiv_id: str, model: str, paper_meta: dict, bib: dict, text: str) -> dict:
    from paper_analyst import SYSTEM_PROMPT, _build_user_message

    result = {
        "arxiv_id": arxiv_id, "model": model,
        "timestamp": datetime.now().isoformat(),
        "latency_s": 0, "parse_errors": [],
        "raw_output": "",         # ← always saved now
        "cn_oneliner":"","cn_abstract":"","contribution_type":"",
        "editorial_note":"","why_read":"","method_variants":[],"core_cite":[],
    }
    try:
        user_msg = _build_user_message(paper_meta, bib, text)
        logger.info(f"  prompt={len(SYSTEM_PROMPT)}c input={len(user_msg)}c")
        raw, lat = llm_call_with_retry(SYSTEM_PROMPT, user_msg, model)
        result["latency_s"]  = round(lat, 2)
        result["raw_output"] = raw

        parsed, err = robust_parse(raw)
        if err:
            result["parse_errors"].append(err)
            logger.warning(f"  parse failed: {err[:80]}")
        else:
            for k in ["cn_oneliner","cn_abstract","contribution_type",
                      "editorial_note","why_read","method_variants","core_cite"]:
                result[k] = parsed.get(k, "")
            logger.info(f"  ✓ {lat:.1f}s  cites={len(result['core_cite'])}")
    except Exception as e:
        result["parse_errors"].append(f"call_error: {e}")
        logger.error(f"  ✗ {e}")
    return result


# ── State ─────────────────────────────────────────────────────────────

def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"cells":{}}

def save_state(s):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(s, indent=2, ensure_ascii=False))

def ck(pid, model): return f"{pid}_{model}"


# ── Run ───────────────────────────────────────────────────────────────

def run_all(only_model=None, only_paper=None, force=False):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()

    papers = [only_paper] if only_paper else PAPERS
    models = [only_model] if only_model else MODELS
    cells  = [(p,m) for p in papers for m in models]
    total  = len(cells)

    paper_cache: dict = {}

    for i, (pid, model) in enumerate(cells):
        key = ck(pid, model)
        if key in state.get("cells",{}) and not force:
            logger.info(f"[{i+1}/{total}] SKIP {key}")
            continue

        # Download paper source (once per paper)
        if pid not in paper_cache:
            meta        = fetch_arxiv_meta(pid)
            bib, text   = download_and_parse(pid)
            paper_cache[pid] = (meta, bib, text)
            time.sleep(2)

        meta, bib, text = paper_cache[pid]

        logger.info(f"\n{'='*60}")
        logger.info(f"[{i+1}/{total}] {pid} × {model}")
        logger.info(f"  title: {meta.get('title','?')[:60]}")
        logger.info(f"{'='*60}")

        result = run_cell(pid, model, meta, bib, text)

        (RESULTS_DIR / f"{key}.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False))

        ok = bool(result.get("cn_oneliner") or result.get("core_cite"))
        state.setdefault("cells",{})[key] = {
            "ok": ok, "latency_s": result["latency_s"],
            "core_cite_count": len(result.get("core_cite",[])),
            "parse_errors": result["parse_errors"],
        }
        save_state(state)
        time.sleep(3)

    logger.info(f"\n✅ Done {total} cells")


# ── Status ────────────────────────────────────────────────────────────

def show_status():
    state = load_state()
    cells = state.get("cells",{})
    done  = sum(1 for v in cells.values() if v.get("ok"))
    total = len(PAPERS) * len(MODELS)
    print(f"\n{done}/{total} cells complete\n")
    print(f"{'Paper':>12} {'Model':>12} {'OK':>4} {'Latency':>8} {'Cites':>6}")
    print("-"*50)
    for pid in PAPERS:
        for model in MODELS:
            k = ck(pid,model)
            if k in cells:
                c = cells[k]
                s = "✓" if c["ok"] else "✗"
                print(f"{pid:>12} {model:>12} {s:>4} {c['latency_s']:>7.1f}s {c['core_cite_count']:>6}")
            else:
                print(f"{pid:>12} {model:>12} {'…':>4}")


# ── HTML ──────────────────────────────────────────────────────────────

def generate_html():
    all_res = {}
    for f in RESULTS_DIR.glob("*.json"):
        d = json.loads(f.read_text())
        # Inline repair if cc=0 but raw_output exists
        if not d.get("core_cite") and d.get("raw_output"):
            parsed, _ = robust_parse(d["raw_output"])
            if parsed:
                for k in ["cn_oneliner","cn_abstract","contribution_type",
                          "editorial_note","why_read","method_variants","core_cite"]:
                    d[k] = parsed.get(k, "")
                d["parse_errors"].append("html_repaired")
                f.write_text(json.dumps(d, indent=2, ensure_ascii=False))
                logger.info(f"  HTML-repair: {f.name} cites={len(d['core_cite'])}")
        all_res[f.stem] = d

    def esc(s): return str(s).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')

    def field_html(val, field):
        if not val: return '<span style="color:#e05c5c">—</span>'
        if field == 'contribution_type':
            col = {'incremental':'#6c8ef5','significant':'#3ecf8e',
                   'story-heavy':'#f5a623','foundational':'#b08ef5'}.get(str(val),'#888')
            return f'<span style="display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:600;background:#1a1d2b;color:{col}">{esc(val)}</span>'
        if field == 'method_variants' and isinstance(val, list):
            return ''.join(
                f'<div style="margin:2px 0;padding:3px 7px;background:#090c14;border-radius:3px;font-size:11px">'
                f'<span style="color:#f5a623;font-family:monospace">{esc(mv.get("variant_tag",""))}</span>'
                f' {esc(mv.get("description",""))}</div>' for mv in val)
        if field == 'core_cite' and isinstance(val, list):
            role_col = {'extends':'#b08ef5','contrasts':'#f59e0b','uses':'#22d3ee',
                        'supports':'#3ecf8e','mentions':'#7a849a'}
            parts = []
            for c in val:
                col = role_col.get(c.get('role',''),'#888')
                parts.append(
                    f'<div style="margin:3px 0;padding:4px 7px;background:#090c14;border-radius:3px;font-size:11px">'
                    f'<span style="font-family:monospace;font-size:10px;padding:1px 5px;border-radius:3px;background:#1e2235;color:{col};margin-right:4px">{c.get("role","?")}</span>'
                    f'<strong>{esc(c.get("title","")[:72])}</strong>'
                    f'<div style="color:#7a849a;font-size:10px">{esc(c.get("note",""))}</div></div>')
            parts.append(f'<div style="font-size:10px;font-family:monospace;color:#7a849a;margin-top:3px">共 {len(val)} 条</div>')
            return ''.join(parts)
        return esc(str(val))

    # Detect highlights: editorial_note 三段 + core_cite ≥ 12 + oneliner ≤ 45
    star_cells = {}
    for key, d in all_res.items():
        reasons = []
        note = d.get("editorial_note","")
        if "[前驱]" in note and "[贡献]" in note and "[判断]" in note:
            reasons.append("editorial_note 三段完整")
        cc = len(d.get("core_cite",[]))
        if cc >= 12: reasons.append(f"core_cite {cc} 条")
        oneliner = d.get("cn_oneliner","")
        if oneliner and ("基于" in oneliner or "把" in oneliner) and len(oneliner) <= 45:
            reasons.append("cn_oneliner 格式精准")
        if reasons: star_cells[key] = reasons

    # Get titles
    paper_titles = {}
    for pid in PAPERS:
        for model in MODELS:
            d = all_res.get(ck(pid,model),{})
            if d.get("cn_oneliner"): break
        meta = fetch_arxiv_meta.__wrapped__(pid) if hasattr(fetch_arxiv_meta,'__wrapped__') else None
        # Try from first available result
        for model in MODELS:
            d = all_res.get(ck(pid,model),{})
            if d.get("arxiv_id"):
                break
        # Try DB
        try:
            import sqlite3
            conn = sqlite3.connect(str(SKILL_DIR/"data"/"paper_network.db"))
            row = conn.execute("SELECT title FROM papers WHERE id=?",[pid]).fetchone()
            conn.close()
            if row: paper_titles[pid] = row[0]; continue
        except: pass
        paper_titles[pid] = pid

    FIELDS = ['cn_oneliner','cn_abstract','contribution_type','editorial_note','why_read','method_variants','core_cite']

    h = []
    h.append('<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8">')
    h.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    h.append('<title>arxiv-radar v3 — E2E Test · 11 Papers × 3 Models</title>')
    h.append('''<style>
:root{--bg:#0d0f18;--card:#141720;--card2:#1a1d2b;--border:#252840;
  --accent:#6c8ef5;--green:#3ecf8e;--yellow:#f5a623;--red:#e05c5c;
  --purple:#b08ef5;--cyan:#22d3ee;--text:#dde3f0;--muted:#7a849a;
  --mono:'JetBrains Mono',monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:-apple-system,sans-serif;font-size:13px;line-height:1.7}
.wrap{max-width:1600px;margin:0 auto;padding:24px 16px}
h1{font-size:20px;color:#fff;margin-bottom:4px}
h2{font-size:14px;color:#fff;margin:26px 0 10px;padding:7px 12px;
   background:var(--card2);border-left:3px solid var(--accent);border-radius:0 4px 4px 0}
.meta{color:var(--muted);font-size:11px;margin-bottom:20px}
table{width:100%;border-collapse:collapse;margin:6px 0;table-layout:fixed}
th{background:#1a1d2b;color:var(--muted);font-size:11px;font-weight:600;
   text-align:left;padding:7px 10px;border-bottom:1px solid var(--border);position:sticky;top:0;z-index:1}
td{padding:7px 10px;border-bottom:1px solid #161928;vertical-align:top;font-size:12px;word-wrap:break-word}
tr:hover td{background:#161928}
.model-h{text-align:center;font-weight:700;font-size:12px}
.lat{font-family:var(--mono);font-size:10px;color:var(--cyan)}
.field-label{font-family:var(--mono);font-size:11px;color:var(--accent);font-weight:700;
             padding:8px 0 4px;border-top:1px solid var(--border);white-space:nowrap}
.star-cell{background:#0e1f14!important;outline:1px solid var(--green);outline-offset:-1px;border-radius:3px}
.star-badge{display:inline-block;padding:1px 5px;border-radius:8px;font-size:10px;
            font-weight:700;background:#1a3a1a;color:var(--green);margin-left:4px;border:1px solid var(--green)}
.toc{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}
.toc a{padding:4px 10px;background:var(--card2);border:1px solid var(--border);
       border-radius:4px;font-size:11px;color:var(--text);text-decoration:none}
.toc a:hover{border-color:var(--accent);color:var(--accent)}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:12px 0}
.stat{background:var(--card);border:1px solid var(--border);border-radius:6px;padding:12px;text-align:center}
.stat .n{font-size:22px;font-weight:700;color:#fff}
.stat .l{font-size:11px;color:var(--muted)}
.warn{color:var(--yellow);font-size:10px;font-family:var(--mono)}
</style></head><body><div class="wrap">''')

    h.append('<h1>arxiv-radar v3 — E2E Test Report</h1>')
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    done = sum(1 for d in all_res.values() if d.get("cn_oneliner"))
    h.append(f'<div class="meta">11 papers × 3 models = 33 cells · {done}/{len(all_res)} 完成 · {now}</div>')

    # Summary
    avg_lat = sum(d.get("latency_s",0) for d in all_res.values()) / max(len(all_res),1)
    avg_cc  = sum(len(d.get("core_cite",[])) for d in all_res.values()) / max(len(all_res),1)
    h.append('<div class="grid3">')
    h.append(f'<div class="stat"><div class="n">{done}/{len(PAPERS)*len(MODELS)}</div><div class="l">完成 cells</div></div>')
    h.append(f'<div class="stat"><div class="n">{avg_lat:.0f}s</div><div class="l">平均延迟</div></div>')
    h.append(f'<div class="stat"><div class="n">{avg_cc:.1f}</div><div class="l">平均 core_cite 数</div></div>')
    h.append('</div>')

    # Model overview
    h.append('<h2>模型总览</h2>')
    h.append('<table><tr><th>模型</th><th>完成/11</th><th>平均延迟</th><th>平均 cites</th><th>[前驱]三段率</th><th>⭐亮点cells</th></tr>')
    for model in MODELS:
        datas = [all_res.get(ck(p,model),{}) for p in PAPERS]
        datas = [d for d in datas if d]
        c_done = sum(1 for d in datas if d.get("cn_oneliner"))
        c_lat  = sum(d.get("latency_s",0) for d in datas) / max(len(datas),1)
        c_cc   = sum(len(d.get("core_cite",[])) for d in datas) / max(len(datas),1)
        c_str  = sum(1 for d in datas if "[前驱]" in (d.get("editorial_note","")) and "[判断]" in (d.get("editorial_note","")))
        c_star = sum(1 for p in PAPERS if ck(p,model) in star_cells)
        h.append(f'<tr><td style="font-weight:700">{model}</td><td>{c_done}/11</td>'
                f'<td class="lat">{c_lat:.1f}s</td><td style="font-family:var(--mono)">{c_cc:.1f}</td>'
                f'<td style="font-family:var(--mono)">{c_str}/11</td><td>{"⭐"*c_star}</td></tr>')
    h.append('</table>')

    # Highlights
    if star_cells:
        h.append('<h2>⭐ 自动标注亮点</h2>')
        h.append('<table><tr><th style="width:120px">Paper</th><th style="width:90px">Model</th><th style="width:160px">亮点原因</th><th>cn_oneliner</th><th>editorial_note（摘要）</th></tr>')
        for key, reasons in list(star_cells.items())[:15]:
            parts = key.rsplit('_',1)
            pid, mdl = (parts[0],parts[1]) if len(parts)==2 else (key,'')
            d = all_res.get(key,{})
            note = d.get("editorial_note","")
            note_short = note[:150]+"…" if len(note)>150 else note
            h.append(f'<tr><td style="font-size:11px">{pid}</td>'
                    f'<td style="font-weight:700">{mdl}</td>'
                    f'<td>{"<br>".join(esc(r) for r in reasons)}</td>'
                    f'<td style="font-size:11px">{esc(d.get("cn_oneliner",""))}</td>'
                    f'<td style="font-size:11px">{esc(note_short)}</td></tr>')
        h.append('</table>')

    # TOC
    h.append('<h2>论文导航</h2><div class="toc">')
    for pid in PAPERS:
        title = paper_titles.get(pid, pid)
        h.append(f'<a href="#{pid}">{pid}<br><span style="font-size:10px;opacity:.6">{title[:28]}</span></a>')
    h.append('</div>')

    # Per-paper
    for pid in PAPERS:
        title = paper_titles.get(pid, pid)
        h.append(f'<h2 id="{pid}">{pid} — {esc(title[:65])}</h2>')
        h.append('<table><tr><th style="width:120px">字段</th>')
        for model in MODELS:
            k = ck(pid,model)
            d = all_res.get(k,{})
            lat = d.get("latency_s",0)
            cc  = len(d.get("core_cite",[]))
            star = "⭐" if k in star_cells else ""
            errs = d.get("parse_errors",[])
            warn = '<br><span class="warn">parse_err</span>' if errs and "repaired" not in str(errs) else ""
            h.append(f'<th class="model-h">{model}{star}<br><span class="lat">{lat:.1f}s · {cc}cites</span>{warn}</th>')
        h.append('</tr>')

        for field in FIELDS:
            h.append(f'<tr><td class="field-label">{field}</td>')
            for model in MODELS:
                k = ck(pid,model)
                d = all_res.get(k,{})
                is_star = k in star_cells
                td_cls = ' class="star-cell"' if is_star else ''
                h.append(f'<td{td_cls}>{field_html(d.get(field), field)}</td>')
            h.append('</tr>')
        h.append('</table>')

    h.append('</div></body></html>')
    HTML_OUT.write_text('\n'.join(h))
    logger.info(f"✓ HTML: {HTML_OUT}  ({HTML_OUT.stat().st_size//1024}KB)")


# ── CLI ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--run",    action="store_true")
    p.add_argument("--status", action="store_true")
    p.add_argument("--html",   action="store_true")
    p.add_argument("--force",  action="store_true")
    p.add_argument("--model",  type=str)
    p.add_argument("--paper",  type=str)
    args = p.parse_args()
    if args.status: show_status()
    elif args.html: generate_html()
    elif args.run:  run_all(only_model=args.model, only_paper=args.paper, force=args.force)
    else:           p.print_help()
