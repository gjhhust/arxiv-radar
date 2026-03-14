"""
repair_parse.py — 对 test_e2e/results/ 中 parse 失败的文件进行后处理修复。
Run after e2e_test.py completes.
"""
import json, re, sys
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"

def _try_fixes(s: str) -> dict | None:
    def _parse(x):
        try: return json.loads(x)
        except: return None
    # Pass 1
    p = _parse(s); 
    if p: return p
    # Pass 2: curly quotes → straight
    s2 = s
    for bad, good in [('\u201c','"'),('\u201d','"'),('\u2018',"'"),('\u2019',"'"),('\u300a',''),('\u300b','')]:
        s2 = s2.replace(bad, good)
    p = _parse(s2)
    if p: return p
    # Pass 3: remove inner CJK-surrounded double quotes
    s3 = re.sub(r'(?<=[\u4e00-\u9fff\w\s])"(?=[\u4e00-\u9fff\w\s])', '', s2)
    p = _parse(s3)
    if p: return p
    # Pass 4: smart inner-quote removal
    result = []
    in_str = False
    escape = False
    for i, ch in enumerate(s2):
        if escape:
            result.append(ch); escape = False
        elif ch == '\\':
            result.append(ch); escape = True
        elif ch == '"':
            if not in_str:
                in_str = True; result.append(ch)
            else:
                rest = s2[i+1:i+20].lstrip()
                if rest and rest[0] in ',:}]':
                    in_str = False; result.append(ch)
                else:
                    pass  # drop inner quote
        else:
            result.append(ch)
    s4 = ''.join(result)
    p = _parse(s4)
    if p: return p
    return None

fixed = 0
failed = 0
ok = 0
for f in sorted(RESULTS_DIR.glob("*.json")):
    d = json.loads(f.read_text())
    if d.get("cn_oneliner") or d.get("core_cite"):
        ok += 1
        continue
    raw = d.get("raw_output", "")
    if not raw:
        failed += 1
        continue
    # Fenced extraction
    m = re.search(r'```(?:json)?\s*\n(.*?)\n```', raw, re.DOTALL)
    candidate = m.group(1) if m else raw
    s = candidate[candidate.find('{'):candidate.rfind('}')+1]
    parsed = _try_fixes(s)
    if parsed:
        d["cn_oneliner"]       = parsed.get("cn_oneliner", "")
        d["cn_abstract"]       = parsed.get("cn_abstract", "")
        d["contribution_type"] = parsed.get("contribution_type", "")
        d["editorial_note"]    = parsed.get("editorial_note", "")
        d["why_read"]          = parsed.get("why_read", "")
        d["method_variants"]   = parsed.get("method_variants", [])
        d["core_cite"]         = parsed.get("core_cite", [])
        d["parse_errors"]      = ["repaired_post_hoc"]
        f.write_text(json.dumps(d, indent=2, ensure_ascii=False))
        print(f"  ✓ REPAIRED {f.name}  cites={len(d['core_cite'])}")
        fixed += 1
    else:
        print(f"  ✗ STILL FAILED {f.name}")
        # Show first 200 chars of raw for debugging
        print(f"    raw: {repr(raw[:200])}")
        failed += 1

print(f"\nSummary: ok={ok}  fixed={fixed}  still_failed={failed}")
