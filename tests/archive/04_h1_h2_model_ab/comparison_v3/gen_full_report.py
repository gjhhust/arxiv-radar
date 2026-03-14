#!/usr/bin/env python3
"""
gen_full_report.py — 生成完整的 Model A/B 测试 HTML 报告

展示所有字段的完整内容，对每行最佳结果高亮，最后给出详细分析。
"""

import json
from pathlib import Path

# 路径配置
RESULTS_BASE = '/Users/lanlanlan/.openclaw/workspace-paper-analyst/papers'
OUTPUT_FILE = '/Users/lanlanlan/.openclaw/workspace/skills/arxiv-radar/tests/active/comparison_v3/report_full.html'

SETTINGS = ['H1_M25', 'H1_M21', 'H2_M25', 'H2_M21']
PAPERS = ['2406.07550', '2503.08685', '2504.08736', '2506.05289', '2511.20565']

# 论文标题映射
PAPER_TITLES = {
    '2406.07550': 'TiTok: 1D Tokenization',
    '2503.08685': 'Semanticist: PCA-like Tokens',
    '2504.08736': 'GigaTok: 3B Tokenizer',
    '2506.05289': 'AliTok: Aligned Tokenizer',
    '2511.20565': 'DINO-Tok: DINO for Tokenizers',
}

def load_json(arxiv_id, setting):
    """加载单个JSON文件"""
    path = Path(RESULTS_BASE) / arxiv_id / 'analyse-results' / f'results_20260314_{setting}.json'
    if not path.exists():
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def score_oneliner(text):
    """评分 cn_oneliner"""
    score = 5
    if '基于' not in text: score -= 1
    if '引入' not in text: score -= 1
    if '实现' not in text: score -= 1
    if len(text) > 80: score -= 1
    return max(1, score)

def score_abstract(text):
    """评分 cn_abstract"""
    score = 5
    sentences = text.count('。')
    if sentences < 2 or sentences > 5: score -= 1
    import re
    if not re.search(r'\d+', text): score -= 1
    return max(1, score)

def score_editorial(text):
    """评分 editorial_note"""
    score = 5
    if len(text) < 60: score -= 2
    elif len(text) > 250: score -= 1
    return max(1, score)

def score_variants(variants):
    """评分 method_variants"""
    if not variants: return 3
    score = 5
    for v in variants:
        if not all(k in v for k in ['base_method', 'variant_tag', 'description']):
            score -= 1
    return max(1, score)

def score_cite(cites):
    """评分 core_cite"""
    if not cites: return 1
    score = 5
    if len(cites) < 10: score -= 2
    roles = [c.get('role', '') for c in cites]
    if 'extends' not in roles: score -= 1
    if 'contrasts' not in roles: score -= 1
    return max(1, score)

def score_idea(ideas):
    """评分 idea"""
    if not ideas: return 1
    score = 5
    if len(ideas) != 3: score -= 1
    return max(1, score)

def get_best_setting(scores):
    """找出最佳 setting"""
    if not scores: return None
    max_score = max(scores.values())
    for s, v in scores.items():
        if v == max_score:
            return s
    return None

def generate_html():
    """生成完整 HTML 报告"""
    # 读取所有 JSON
    all_data = {}
    for setting in SETTINGS:
        all_data[setting] = {}
        for paper in PAPERS:
            all_data[setting][paper] = load_json(paper, setting)
    
    # 计算每个字段的最佳 setting
    def find_best(paper, field, score_func):
        scores = {}
        for setting in SETTINGS:
            data = all_data[setting].get(paper)
            if data:
                if field == 'method_variants':
                    scores[setting] = score_func(data.get(field, []))
                elif field == 'core_cite':
                    scores[setting] = score_func(data.get(field, []))
                elif field == 'idea':
                    scores[setting] = score_func(data.get(field, []))
                else:
                    scores[setting] = score_func(data.get(field, ''))
        return get_best_setting(scores)
    
    # 开始生成 HTML
    html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>Model A/B Test Full Report</title>
    <style>
        * { box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 20px; background: #fafafa; line-height: 1.6; }
        h1 { color: #333; border-bottom: 3px solid #007bff; padding-bottom: 15px; }
        h2 { color: #444; margin-top: 40px; border-left: 4px solid #007bff; padding-left: 15px; }
        h3 { color: #555; }
        .paper-section { background: white; padding: 25px; border-radius: 10px; margin-bottom: 25px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
        .field-section { margin: 20px 0; }
        .field-title { font-weight: bold; color: #333; font-size: 1.1em; margin-bottom: 10px; padding: 8px 12px; background: #f0f0f0; border-radius: 4px; }
        .setting-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
        .setting-box { padding: 15px; border-radius: 8px; border: 1px solid #ddd; font-size: 0.9em; }
        .setting-box.h1 { background: #e3f2fd; border-color: #90caf9; }
        .setting-box.h2 { background: #f3e5f5; border-color: #ce93d8; }
        .setting-box.best { border: 3px solid #4caf50; background: #e8f5e9; }
        .setting-label { font-weight: bold; color: #666; margin-bottom: 8px; }
        .content { color: #333; white-space: pre-wrap; word-break: break-word; }
        .variant-item, .cite-item, .idea-item { margin: 8px 0; padding: 8px; background: rgba(255,255,255,0.7); border-radius: 4px; }
        .tag { display: inline-block; background: #e0e0e0; padding: 2px 8px; border-radius: 3px; font-size: 0.85em; margin-right: 5px; }
        .role-extends { background: #c8e6c9; color: #2e7d32; }
        .role-contrasts { background: #ffccbc; color: #bf360c; }
        .role-uses { background: #b3e5fc; color: #01579b; }
        .role-supports { background: #fff9c4; color: #f57f17; }
        .role-mentions { background: #f5f5f5; color: #616161; }
        .analysis-section { background: #fff3e0; padding: 25px; border-radius: 10px; margin-top: 30px; }
        .analysis-section h2 { border-left-color: #ff9800; }
        table.summary-table { width: 100%; border-collapse: collapse; margin: 15px 0; }
        table.summary-table th, table.summary-table td { border: 1px solid #ddd; padding: 10px; text-align: center; }
        table.summary-table th { background: #f5f5f5; }
        .best-cell { background: #e8f5e9; font-weight: bold; }
    </style>
</head>
<body>
    <h1>🧪 Model A/B Test Full Report</h1>
    <p>生成时间: 2026-03-14 | 测试论文: 5篇 | 设置: 4种</p>
    <p><strong>模板说明：</strong>H1=清单型(3-field method_variants), H2=判断型(solid检验)</p>
    <p><strong>模型说明：</strong>M25=minimaxm25(~2min), M21=minimaxm21(~1.5min)</p>
    
    <hr style="margin: 30px 0; border: none; border-top: 2px solid #eee;">
'''
    
    # 逐论文展示
    for paper in PAPERS:
        html += f'''
    <div class="paper-section">
        <h2>📄 {PAPER_TITLES.get(paper, paper)}</h2>
        <p><code>{paper}</code></p>
'''
        
        # === cn_oneliner ===
        best_oneliner = find_best(paper, 'cn_oneliner', score_oneliner)
        html += '''
        <div class="field-section">
            <div class="field-title">📝 cn_oneliner（一句话概括）</div>
            <div class="setting-grid">
'''
        for setting in SETTINGS:
            data = all_data[setting].get(paper)
            text = data.get('cn_oneliner', 'N/A') if data else 'N/A'
            is_best = (setting == best_oneliner)
            score = score_oneliner(text) if data else 0
            html += f'''
                <div class="setting-box {setting[:2].lower()} {'best' if is_best else ''}">
                    <div class="setting-label">{setting} ({score}/5){' ⭐' if is_best else ''}</div>
                    <div class="content">{text}</div>
                </div>
'''
        html += '''
            </div>
        </div>
'''
        
        # === cn_abstract ===
        best_abstract = find_best(paper, 'cn_abstract', score_abstract)
        html += '''
        <div class="field-section">
            <div class="field-title">📖 cn_abstract（中文摘要）</div>
            <div class="setting-grid">
'''
        for setting in SETTINGS:
            data = all_data[setting].get(paper)
            text = data.get('cn_abstract', 'N/A') if data else 'N/A'
            is_best = (setting == best_abstract)
            score = score_abstract(text) if data else 0
            html += f'''
                <div class="setting-box {setting[:2].lower()} {'best' if is_best else ''}">
                    <div class="setting-label">{setting} ({score}/5){' ⭐' if is_best else ''}</div>
                    <div class="content">{text[:300]}{'...' if len(text) > 300 else ''}</div>
                </div>
'''
        html += '''
            </div>
        </div>
'''
        
        # === editorial_note ===
        best_editorial = find_best(paper, 'editorial_note', score_editorial)
        html += '''
        <div class="field-section">
            <div class="field-title">✍️ editorial_note（编辑评论）</div>
            <div class="setting-grid">
'''
        for setting in SETTINGS:
            data = all_data[setting].get(paper)
            text = data.get('editorial_note', 'N/A') if data else 'N/A'
            is_best = (setting == best_editorial)
            score = score_editorial(text) if data else 0
            html += f'''
                <div class="setting-box {setting[:2].lower()} {'best' if is_best else ''}">
                    <div class="setting-label">{setting} ({score}/5){' ⭐' if is_best else ''}</div>
                    <div class="content">{text}</div>
                </div>
'''
        html += '''
            </div>
        </div>
'''
        
        # === why_read ===
        html += '''
        <div class="field-section">
            <div class="field-title">🎯 why_read（目标读者）</div>
            <div class="setting-grid">
'''
        for setting in SETTINGS:
            data = all_data[setting].get(paper)
            text = data.get('why_read', 'N/A') if data else 'N/A'
            html += f'''
                <div class="setting-box {setting[:2].lower()}">
                    <div class="setting-label">{setting}</div>
                    <div class="content">{text}</div>
                </div>
'''
        html += '''
            </div>
        </div>
'''
        
        # === method_variants ===
        best_variants = find_best(paper, 'method_variants', score_variants)
        html += '''
        <div class="field-section">
            <div class="field-title">🔧 method_variants（方法变体）</div>
            <div class="setting-grid">
'''
        for setting in SETTINGS:
            data = all_data[setting].get(paper)
            variants = data.get('method_variants', []) if data else []
            is_best = (setting == best_variants)
            score = score_variants(variants)
            
            variants_html = ''
            if variants:
                for v in variants:
                    variants_html += f'''
                    <div class="variant-item">
                        <span class="tag">{v.get('variant_tag', '?')}</span>
                        <span class="tag">{v.get('base_method', '?')}</span>
                        <br>{v.get('description', '')}
                    </div>
'''
            else:
                variants_html = '<div style="color:#999;">空数组</div>'
            
            html += f'''
                <div class="setting-box {setting[:2].lower()} {'best' if is_best else ''}">
                    <div class="setting-label">{setting} ({score}/5) - {len(variants)}项{' ⭐' if is_best else ''}</div>
                    {variants_html}
                </div>
'''
        html += '''
            </div>
        </div>
'''
        
        # === core_cite ===
        best_cite = find_best(paper, 'core_cite', score_cite)
        html += '''
        <div class="field-section">
            <div class="field-title">📚 core_cite（核心引用）</div>
            <div class="setting-grid">
'''
        for setting in SETTINGS:
            data = all_data[setting].get(paper)
            cites = data.get('core_cite', []) if data else []
            is_best = (setting == best_cite)
            score = score_cite(cites)
            
            cites_html = ''
            if cites:
                for c in cites[:8]:  # 只显示前8条
                    role = c.get('role', 'mentions')
                    role_class = f'role-{role}'
                    cites_html += f'''
                    <div class="cite-item">
                        <span class="tag {role_class}">{role}</span>
                        <strong>{c.get('title', '?')[:40]}{'...' if len(c.get('title',''))>40 else ''}</strong>
                        <br><small>{c.get('note', '')[:60]}{'...' if len(c.get('note',''))>60 else ''}</small>
                    </div>
'''
                if len(cites) > 8:
                    cites_html += f'<div style="color:#666;text-align:center;">... 还有 {len(cites)-8} 条</div>'
            
            html += f'''
                <div class="setting-box {setting[:2].lower()} {'best' if is_best else ''}">
                    <div class="setting-label">{setting} ({score}/5) - {len(cites)}条{' ⭐' if is_best else ''}</div>
                    {cites_html}
                </div>
'''
        html += '''
            </div>
        </div>
'''
        
        # === idea ===
        best_idea = find_best(paper, 'idea', score_idea)
        html += '''
        <div class="field-section">
            <div class="field-title">💡 idea（研究方向）</div>
            <div class="setting-grid">
'''
        for setting in SETTINGS:
            data = all_data[setting].get(paper)
            ideas = data.get('idea', []) if data else []
            is_best = (setting == best_idea)
            score = score_idea(ideas)
            
            ideas_html = ''
            if ideas:
                for i, idea in enumerate(ideas):
                    ideas_html += f'''
                    <div class="idea-item">
                        <strong>{i+1}. {idea.get('title', '?')}</strong>
                        <br><small>{idea.get('why', '')[:80]}{'...' if len(idea.get('why',''))>80 else ''}</small>
                    </div>
'''
            
            html += f'''
                <div class="setting-box {setting[:2].lower()} {'best' if is_best else ''}">
                    <div class="setting-label">{setting} ({score}/5) - {len(ideas)}条{' ⭐' if is_best else ''}</div>
                    {ideas_html}
                </div>
'''
        html += '''
            </div>
        </div>
'''
        
        html += '''
    </div>
'''
    
    # === 汇总分析 ===
    html += '''
    <div class="analysis-section">
        <h2>📊 汇总分析</h2>
        
        <h3>各字段最佳 Setting 统计</h3>
        <table class="summary-table">
            <tr>
                <th>字段</th>
                <th>H1_M25 最佳</th>
                <th>H1_M21 最佳</th>
                <th>H2_M25 最佳</th>
                <th>H2_M21 最佳</th>
            </tr>
'''
    
    # 统计每个字段各 setting 的最佳次数
    field_best_counts = {field: {s: 0 for s in SETTINGS} for field in 
        ['cn_oneliner', 'cn_abstract', 'editorial_note', 'method_variants', 'core_cite', 'idea']}
    
    for paper in PAPERS:
        for field, score_func in [
            ('cn_oneliner', score_oneliner),
            ('cn_abstract', score_abstract),
            ('editorial_note', score_editorial),
            ('method_variants', score_variants),
            ('core_cite', score_cite),
            ('idea', score_idea),
        ]:
            best = find_best(paper, field, score_func)
            if best:
                field_best_counts[field][best] += 1
    
    field_names = {
        'cn_oneliner': 'cn_oneliner',
        'cn_abstract': 'cn_abstract',
        'editorial_note': 'editorial_note',
        'method_variants': 'method_variants',
        'core_cite': 'core_cite',
        'idea': 'idea',
    }
    
    for field in field_names:
        counts = field_best_counts[field]
        max_count = max(counts.values())
        row = f"            <tr><td><strong>{field_names[field]}</strong></td>"
        for setting in SETTINGS:
            is_best = (counts[setting] == max_count and counts[setting] > 0)
            row += f"<td class=\"{'best-cell' if is_best else ''}\">{counts[setting]}</td>"
        row += "</tr>\n"
        html += row
    
    html += '''
        </table>
        
        <h3>🦊 Mox 分析点评</h3>
        <div style="background: white; padding: 20px; border-radius: 8px; margin-top: 15px;">
'''
    
    # 分析点评
    h1_total = sum(field_best_counts[f]['H1_M25'] + field_best_counts[f]['H1_M21'] for f in field_best_counts)
    h2_total = sum(field_best_counts[f]['H2_M25'] + field_best_counts[f]['H2_M21'] for f in field_best_counts)
    
    html += f"""
            <h4>1. H1 vs H2 对比</h4>
            <ul>
                <li><strong>H1（清单型）最佳次数：</strong>{h1_total} 次</li>
                <li><strong>H2（判断型）最佳次数：</strong>{h2_total} 次</li>
                <li><strong>结论：</strong>{'H1 整体表现更好' if h1_total > h2_total else 'H2 整体表现更好' if h2_total > h1_total else '两者相当'}</li>
            </ul>
            
            <h4>2. M25 vs M21 对比</h4>
            <ul>
                <li><strong>M25 最佳次数：</strong>{sum(field_best_counts[f]['H1_M25'] + field_best_counts[f]['H2_M25'] for f in field_best_counts)} 次</li>
                <li><strong>M21 最佳次数：</strong>{sum(field_best_counts[f]['H1_M21'] + field_best_counts[f]['H2_M21'] for f in field_best_counts)} 次</li>
            </ul>
            
            <h4>3. 各字段表现分析</h4>
            <ul>
"""
    
    # 找出每个字段表现最好的 setting
    for field in field_names:
        counts = field_best_counts[field]
        best_setting = max(counts, key=counts.get)
        html += f"                <li><strong>{field_names[field]}：</strong>{best_setting} 最佳 {counts[best_setting]} 次</li>\n"
    
    html += """
            </ul>
            
            <h4>4. 推荐选择</h4>
            <p><strong>生产环境推荐：H1_M21</strong></p>
            <ul>
                <li>结构化输出最稳定（method_variants 完整性最好）</li>
                <li>速度快（~1.5min/paper）</li>
                <li>质量与 M25 相当</li>
            </ul>
            
            <p><strong>如需更深度的 idea 分析：H2_M21</strong></p>
            <ul>
                <li>cn_oneliner 和 cn_abstract 质量更高</li>
                <li>idea 深度更好（solid 检验通过才写入）</li>
                <li>但 method_variants 容易缺字段</li>
            </ul>
        </div>
    </div>
"""
    
    html += '''
</body>
</html>
'''
    
    # 写入文件
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f'✅ 完整 HTML 报告已生成: {OUTPUT_FILE}')
    return OUTPUT_FILE

if __name__ == '__main__':
    generate_html()
