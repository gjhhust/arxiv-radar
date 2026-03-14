import json, re
from pathlib import Path

base = Path('/Users/lanlanlan/.openclaw/workspace-paper-analyst/papers')
out_path = Path('/Users/lanlanlan/.openclaw/workspace/skills/arxiv-radar/tests/active/comparison_v3/report_model_ab.html')

SETTINGS = [
    ('H1_M25', 'H1 · M25', '#e8f4f8'),
    ('H1_M21', 'H1 · M21', '#fef9e7'),
    ('H2_M25', 'H2 · M25', '#e8f8f0'),
    ('H2_M21', 'H2 · M21', '#fde8f0'),
]
PAPERS = ['2406.07550','2503.08685','2504.08736','2506.05289','2511.20565']

# Load all data
data = {}
for pid in PAPERS:
    data[pid] = {}
    for sid, slabel, _ in SETTINGS:
        f = base / pid / 'analyse-results' / 'results_20260314_{}.json'.format(sid)
        if f.exists():
            data[pid][sid] = json.loads(f.read_text())
        else:
            data[pid][sid] = {}

def esc(s):
    if not isinstance(s, str):
        s = json.dumps(s, ensure_ascii=False)
    return s.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')

def score_field(vals, field):
    results = {}
    if field == 'cn_oneliner':
        for k, v in vals.items():
            l = len(v) if v else 999
            if l <= 45:
                results[k] = 'best'
            elif l <= 65:
                results[k] = 'ok'
            else:
                results[k] = 'weak'
    elif field == 'core_cite_count':
        for k, v in vals.items():
            if v >= 13:
                results[k] = 'best'
            elif v >= 10:
                results[k] = 'ok'
            else:
                results[k] = 'weak'
    elif field == 'idea_quality':
        for k, v in vals.items():
            if v and len(v) == 3:
                avg_len = sum(len(i.get('why','')) for i in v) / 3
                results[k] = 'best' if avg_len >= 60 else 'ok'
            else:
                results[k] = 'weak'
    elif field == 'editorial_note':
        for k, v in vals.items():
            l = len(v) if v else 0
            if l >= 120:
                results[k] = 'best'
            elif l >= 80:
                results[k] = 'ok'
            else:
                results[k] = 'weak'
    elif field == 'method_variants':
        for k, v in vals.items():
            if isinstance(v, list) and len(v) > 0:
                avg = sum(len(str(x)) for x in v) / len(v)
                results[k] = 'best' if avg >= 60 else 'ok'
            else:
                results[k] = 'ok'
    return results

def badge(r):
    colors = {'best': '#27ae60', 'ok': '#f39c12', 'weak': '#e74c3c'}
    labels = {'best': '★ Best', 'ok': '~ OK', 'weak': '✗ Weak'}
    c = colors.get(r, '#95a5a6')
    l = labels.get(r, r)
    return '<span style="background:{};color:white;padding:1px 6px;border-radius:3px;font-size:11px">{}</span>'.format(c, l)

def render_list(lst):
    if not lst:
        return '<em style="color:#aaa">empty</em>'
    if isinstance(lst, list):
        items = []
        for item in lst:
            if isinstance(item, dict):
                parts = []
                for k, v in item.items():
                    parts.append('<b>{}:</b> {}'.format(esc(k), esc(str(v))))
                items.append('<li style="margin-bottom:4px">{}</li>'.format(' · '.join(parts)))
            else:
                items.append('<li>{}</li>'.format(esc(str(item))))
        return '<ul style="margin:0;padding-left:16px">' + ''.join(items) + '</ul>'
    return esc(str(lst))

CSS = '''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>Model A/B Report — H1/H2 x M25/M21</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:13px;background:#f5f5f5;margin:0;padding:20px}
h1{font-size:22px;color:#2c3e50;margin-bottom:4px}
.subtitle{color:#666;font-size:13px;margin-bottom:24px}
.paper-section{background:white;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.1);margin-bottom:28px;overflow:hidden}
.paper-header{background:#2c3e50;color:white;padding:10px 16px;font-size:14px;font-weight:bold}
.paper-header small{font-weight:normal;opacity:.8;margin-left:8px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);border-top:1px solid #eee}
.cell{padding:10px 12px;border-right:1px solid #eee;border-bottom:1px solid #eee}
.cell:last-child{border-right:none}
.field-label{background:#34495e;color:white;padding:4px 12px;font-size:11px;font-weight:bold;margin:0;grid-column:1/-1;border-bottom:1px solid #eee}
.col-header{background:#ecf0f1;font-size:11px;font-weight:bold;padding:6px 12px;text-align:center;border-bottom:2px solid #bdc3c7;grid-column:span 1}
.note-text{font-size:12px;line-height:1.6;white-space:pre-wrap;word-break:break-word}
.small{font-size:11px;color:#555;line-height:1.5}
table.summary{border-collapse:collapse;width:100%;margin-top:20px}
table.summary th,table.summary td{border:1px solid #ddd;padding:8px 12px;text-align:center}
table.summary th{background:#2c3e50;color:white}
table.summary tr:nth-child(even){background:#f9f9f9}
.winner{background:#d5f5e3!important;font-weight:bold}
.oneliner{font-size:12px;line-height:1.5;word-break:break-all}
</style>
</head>
<body>
<h1>Model A/B Test Report</h1>
<div class="subtitle">H1 vs H2 scheme x minimaxm25 vs minimaxm21 | 5 papers | Generated by Mox</div>
'''

html_parts = [CSS]

summary_scores = {sid: {'best': 0, 'ok': 0, 'weak': 0} for sid, _, _ in SETTINGS}

for pid in PAPERS:
    titles = [data[pid][s].get('title', pid) for s, _, _ in SETTINGS if data[pid].get(s)]
    title = titles[0] if titles else pid
    html_parts.append('<div class="paper-section">')
    html_parts.append('<div class="paper-header">{} <small>{}</small></div>'.format(esc(pid), esc(title)))

    # Column headers
    html_parts.append('<div class="grid">')
    html_parts.append('<div class="col-header">H1 · minimaxm25</div>')
    html_parts.append('<div class="col-header">H1 · minimaxm21</div>')
    html_parts.append('<div class="col-header">H2 · minimaxm25</div>')
    html_parts.append('<div class="col-header">H2 · minimaxm21</div>')
    html_parts.append('</div>')

    # cn_oneliner
    vals = {sid: data[pid].get(sid, {}).get('cn_oneliner', '') for sid, _, _ in SETTINGS}
    ratings = score_field(vals, 'cn_oneliner')
    for sid, r in ratings.items():
        summary_scores[sid][r] += 1
    html_parts.append('<div class="grid"><div class="field-label">cn_oneliner (<=45 chars)</div>')
    for sid, slabel, bg in SETTINGS:
        v = vals[sid]
        l = len(v)
        r = ratings[sid]
        color = '#d5f5e3' if r == 'best' else ('#fef9e7' if r == 'ok' else '#fde8f0')
        html_parts.append('<div class="cell" style="background:{}"><div style="margin-bottom:4px">{} <span style="font-size:10px;color:#888">{}chars</span></div><div class="oneliner">{}</div></div>'.format(
            color, badge(r), l, esc(v)))
    html_parts.append('</div>')

    # editorial_note
    vals = {sid: data[pid].get(sid, {}).get('editorial_note', '') for sid, _, _ in SETTINGS}
    ratings = score_field(vals, 'editorial_note')
    for sid, r in ratings.items():
        summary_scores[sid][r] += 1
    html_parts.append('<div class="grid"><div class="field-label">editorial_note</div>')
    for sid, slabel, bg in SETTINGS:
        v = vals[sid]
        r = ratings[sid]
        color = '#d5f5e3' if r == 'best' else ('#fef9e7' if r == 'ok' else '#fde8f0')
        html_parts.append('<div class="cell" style="background:{}"><div style="margin-bottom:4px">{}</div><div class="note-text small">{}</div></div>'.format(
            color, badge(r), esc(v)))
    html_parts.append('</div>')

    # why_read
    html_parts.append('<div class="grid"><div class="field-label">why_read</div>')
    for sid, slabel, bg in SETTINGS:
        v = data[pid].get(sid, {}).get('why_read', '')
        html_parts.append('<div class="cell"><div class="small">{}</div></div>'.format(esc(v)))
    html_parts.append('</div>')

    # method_variants
    vals = {sid: data[pid].get(sid, {}).get('method_variants', []) for sid, _, _ in SETTINGS}
    ratings = score_field(vals, 'method_variants')
    for sid, r in ratings.items():
        summary_scores[sid][r] += 1
    html_parts.append('<div class="grid"><div class="field-label">method_variants</div>')
    for sid, slabel, bg in SETTINGS:
        v = vals[sid]
        r = ratings[sid]
        color = '#d5f5e3' if r == 'best' else ('#fef9e7' if r == 'ok' else '#fde8f0')
        cnt = len(v) if isinstance(v, list) else 0
        html_parts.append('<div class="cell" style="background:{}"><div style="margin-bottom:4px">{} <span style="font-size:10px;color:#666">({} items)</span></div>{}</div>'.format(
            color, badge(r), cnt, render_list(v)))
    html_parts.append('</div>')

    # core_cite
    vals_n = {sid: len(data[pid].get(sid, {}).get('core_cite', [])) for sid, _, _ in SETTINGS}
    ratings = score_field(vals_n, 'core_cite_count')
    for sid, r in ratings.items():
        summary_scores[sid][r] += 1
    html_parts.append('<div class="grid"><div class="field-label">core_cite (count + role distribution)</div>')
    for sid, slabel, bg in SETTINGS:
        cites = data[pid].get(sid, {}).get('core_cite', [])
        r = ratings[sid]
        color = '#d5f5e3' if r == 'best' else ('#fef9e7' if r == 'ok' else '#fde8f0')
        roles = {}
        for c in cites:
            ro = c.get('role', '?')
            roles[ro] = roles.get(ro, 0) + 1
        role_str = ' | '.join('{}: {}'.format(k, v) for k, v in sorted(roles.items()))
        top3 = ''
        for c in cites[:3]:
            top3 += '<li><b>{}</b> [{}]<br><span style="color:#777">{}</span></li>'.format(
                esc(c.get('title', '')[:50]), esc(c.get('role', '')), esc(c.get('note', '')[:60]))
        html_parts.append('<div class="cell" style="background:{}"><div style="margin-bottom:4px">{} <b style="font-size:12px">{} items</b><br><span style="font-size:10px;color:#666">{}</span></div><ul style="margin:4px 0 0;padding-left:14px;font-size:11px">{}</ul>{}</div>'.format(
            color, badge(r), len(cites), esc(role_str), top3,
            '<div style="font-size:10px;color:#aaa;margin-top:2px">...and {} more</div>'.format(len(cites)-3) if len(cites) > 3 else ''))
    html_parts.append('</div>')

    # idea
    vals = {sid: data[pid].get(sid, {}).get('idea', []) for sid, _, _ in SETTINGS}
    ratings = score_field(vals, 'idea_quality')
    for sid, r in ratings.items():
        summary_scores[sid][r] += 1
    html_parts.append('<div class="grid"><div class="field-label">idea (3 items: A/B/C)</div>')
    for sid, slabel, bg in SETTINGS:
        v = vals[sid]
        r = ratings[sid]
        color = '#d5f5e3' if r == 'best' else ('#fef9e7' if r == 'ok' else '#fde8f0')
        items = ''
        for i, idea in enumerate(v or []):
            lbl = ['A', 'B', 'C'][i] if i < 3 else str(i+1)
            items += '<li style="margin-bottom:6px"><b>[{}] {}</b><br><span style="color:#555;font-size:10px">{}</span></li>'.format(
                lbl, esc(idea.get('title', '')), esc(idea.get('why', '')))
        html_parts.append('<div class="cell" style="background:{}"><div style="margin-bottom:4px">{}</div><ul style="margin:0;padding-left:14px;font-size:11px">{}</ul></div>'.format(
            color, badge(r), items))
    html_parts.append('</div>')

    html_parts.append('</div>')  # paper-section

# Summary table
html_parts.append('<h2 style="color:#2c3e50;margin-top:32px">Summary Score (AI self-eval)</h2>')
html_parts.append('<table class="summary"><tr><th>Setting</th><th>Scheme</th><th>Model</th><th>Best</th><th>OK</th><th>Weak</th><th>Score (best=2, ok=1, weak=0)</th></tr>')

scores_list = []
scheme_map = {'H1_M25': 'H1', 'H1_M21': 'H1', 'H2_M25': 'H2', 'H2_M21': 'H2'}
model_map = {'H1_M25': 'minimaxm25', 'H1_M21': 'minimaxm21', 'H2_M25': 'minimaxm25', 'H2_M21': 'minimaxm21'}

for sid, slabel, _ in SETTINGS:
    b = summary_scores[sid]['best']
    o = summary_scores[sid]['ok']
    w = summary_scores[sid]['weak']
    total = b * 2 + o * 1
    scores_list.append((sid, slabel, b, o, w, total))

max_score = max(x[5] for x in scores_list)
for sid, slabel, b, o, w, total in scores_list:
    is_winner = (total == max_score)
    tr_class = ' class="winner"' if is_winner else ''
    html_parts.append('<tr{}><td><b>{}</b>{}</td><td>{}</td><td>{}</td><td style="color:#27ae60;font-weight:bold">{}</td><td style="color:#f39c12">{}</td><td style="color:#e74c3c">{}</td><td style="font-size:16px;font-weight:bold">{}</td></tr>'.format(
        tr_class, slabel,
        ' WINNER' if is_winner else '',
        scheme_map[sid], model_map[sid], b, o, w, total))
html_parts.append('</table>')

# Mox judgment
html_parts.append('''
<div style="background:#eaf4fb;border-left:4px solid #3498db;padding:14px 18px;margin-top:20px;border-radius:0 6px 6px 0">
<b>Mox Editorial Notes</b><br><br>
<b>cn_oneliner:</b> M21 tends to be more concise and within the 45-char limit; M25 sometimes exceeds but includes more info.<br>
<b>editorial_note:</b> H2 three-section structure (prior / contribution / judgment) is cleaner with stronger editorial judgment; H1 is more descriptive.<br>
<b>method_variants:</b> H1 three-field format (base_method + variant_tag + description) is more structured; H2 two-field includes richer insight analysis per variant.<br>
<b>core_cite:</b> Both schemes stable at >=10; M21 slightly higher average cite count. Role distribution (extends/contrasts) consistently present.<br>
<b>idea:</b> H2 solid-test filter yields higher quality ideas (longer "why" text, more specific gaps); H1 A/B/C categorization is more mechanical.<br><br>
<b>Recommendation:</b> For format compliance -> H1xM21; for content depth / editorial judgment -> H2xM21; H2xM25 produces the most nuanced editorial_note.
</div>
</body></html>
''')

out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(''.join(html_parts), encoding='utf-8')
print('Done: {} ({} bytes)'.format(out_path, out_path.stat().st_size))
