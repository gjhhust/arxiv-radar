#!/usr/bin/env python3
"""
gen_final_report.py — 生成 Model A/B 测试的 HTML 报告

读取所有 20 个 JSON 文件 (4 settings × 5 papers)：
- H1_M25, H1_M21 (清单型模板)
- H2_M25, H2_M21 (判断型模板)

对每个字段进行内容质量评估，生成 HTML 报告。
"""

import json
from pathlib import Path

# 路径配置
RESULTS_BASE = '/Users/lanlanlan/.openclaw/workspace-paper-analyst/papers'
OUTPUT_FILE = '/Users/lanlanlan/.openclaw/workspace/skills/arxiv-radar/tests/active/comparison_v3/report_final.html'

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

def evaluate_oneliner(text):
    """评估 cn_oneliner 质量"""
    issues = []
    score = 5
    
    # 检查格式：「基于...，引入...，实现...」
    if '基于' not in text:
        issues.append('缺少「基于」前驱方法')
        score -= 1
    if '引入' not in text:
        issues.append('缺少「引入」核心改动')
        score -= 1
    if '实现' not in text:
        issues.append('缺少「实现」效果指标')
        score -= 1
    
    # 检查长度（45字限制）
    if len(text) > 60:
        issues.append(f'字数超限 ({len(text)}字)')
        score -= 1
    
    return max(1, score), issues

def evaluate_abstract(text):
    """评估 cn_abstract 质量"""
    issues = []
    score = 5
    
    # 检查句子数量（2-4句）
    sentences = text.count('。')
    if sentences < 2:
        issues.append(f'句子过少 ({sentences}句)')
        score -= 1
    elif sentences > 5:
        issues.append(f'句子过多 ({sentences}句)')
        score -= 1
    
    # 检查是否包含数值
    import re
    if not re.search(r'\d+', text):
        issues.append('缺少数值指标')
        score -= 1
    
    return max(1, score), issues

def evaluate_editorial(text):
    """评估 editorial_note 质量"""
    issues = []
    score = 5
    
    # 检查三段结构（通过句号判断）
    length = len(text)
    if length < 60:
        issues.append(f'字数过少 ({length}字)')
        score -= 2
    elif length > 200:
        issues.append(f'字数过多 ({length}字)')
        score -= 1
    
    # 检查是否包含「局限」「解决」「判断」等关键词
    keywords = ['局限', '问题', '解决', '判断', '创新']
    found = sum(1 for k in keywords if k in text)
    if found < 2:
        issues.append('缺少关键判断词')
        score -= 1
    
    return max(1, score), issues

def evaluate_method_variants(variants):
    """评估 method_variants 质量"""
    issues = []
    score = 5
    
    if not variants:
        issues.append('method_variants 为空')
        return 3, issues  # 空数组不一定是错误
    
    # 检查每项是否有三个字段
    for v in variants:
        if not all(k in v for k in ['base_method', 'variant_tag', 'description']):
            issues.append(f'缺少字段: {v.get("base_method", "?")}')
            score -= 1
    
    # 检查数量是否合理（2-5个）
    if len(variants) > 6:
        issues.append(f'variants 过多 ({len(variants)}个)')
        score -= 1
    
    return max(1, score), issues

def evaluate_core_cite(cites, refs_list):
    """评估 core_cite 质量"""
    issues = []
    score = 5
    
    if not cites:
        issues.append('core_cite 为空')
        return 1, issues
    
    # 检查数量（≥10）
    if len(cites) < 10:
        issues.append(f'引用数量不足 ({len(cites)}条)')
        score -= 2
    
    # 检查是否有 extends/contrasts
    roles = [c.get('role', '') for c in cites]
    if 'extends' not in roles:
        issues.append('缺少 extends 类引用')
        score -= 1
    if 'contrasts' not in roles:
        issues.append('缺少 contrasts 类引用')
        score -= 1
    
    # 检查是否都在参考列表中（简化检查）
    for c in cites:
        if not c.get('arxiv_id') and not c.get('title'):
            issues.append(f'引用缺少标识: {c}')
            score -= 0.5
    
    return max(1, int(score)), issues

def evaluate_idea(ideas):
    """评估 idea 质量"""
    issues = []
    score = 5
    
    if not ideas:
        issues.append('idea 为空')
        return 1, issues
    
    # 检查数量（恰好3条）
    if len(ideas) != 3:
        issues.append(f'idea 数量应为3条 ({len(ideas)}条)')
        score -= 1
    
    # 检查是否是未来方向而非本文贡献
    for idea in ideas:
        title = idea.get('title', '')
        why = idea.get('why', '')
        # 检查是否包含"本文已做"的关键词
        if '本文提出' in title or '本文引入' in title:
            issues.append(f'idea 疑似本文贡献: {title[:20]}...')
            score -= 2
    
    return max(1, score), issues

def generate_html():
    """生成 HTML 报告"""
    # 读取所有 JSON
    all_data = {}
    for setting in SETTINGS:
        all_data[setting] = {}
        for paper in PAPERS:
            all_data[setting][paper] = load_json(paper, setting)
    
    # 开始生成 HTML
    html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>Model A/B Test Final Report</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 20px; background: #f5f5f5; }
        h1 { color: #333; border-bottom: 2px solid #007bff; padding-bottom: 10px; }
        h2 { color: #555; margin-top: 30px; }
        h3 { color: #666; }
        .summary { background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .paper-card { background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .setting-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 15px; }
        .setting-box { padding: 15px; border-radius: 6px; border: 1px solid #ddd; }
        .setting-box.h1 { background: #e3f2fd; }
        .setting-box.h2 { background: #f3e5f5; }
        .score { font-weight: bold; font-size: 1.2em; }
        .score.high { color: #28a745; }
        .score.medium { color: #ffc107; }
        .score.low { color: #dc3545; }
        .issue { color: #dc3545; font-size: 0.9em; }
        .highlight { background: #fff3cd; padding: 2px 5px; border-radius: 3px; }
        .best { background: #d4edda; padding: 10px; border-radius: 6px; margin: 10px 0; }
        .worst { background: #f8d7da; padding: 10px; border-radius: 6px; margin: 10px 0; }
        table { width: 100%; border-collapse: collapse; margin: 15px 0; }
        th, td { border: 1px solid #ddd; padding: 10px; text-align: left; }
        th { background: #f8f9fa; }
        .field-label { font-weight: bold; color: #555; }
        pre { background: #f8f9fa; padding: 10px; border-radius: 4px; overflow-x: auto; font-size: 0.85em; }
    </style>
</head>
<body>
    <h1>🧪 Model A/B Test Final Report</h1>
    <p>生成时间: 2026-03-14 | 测试论文: 5篇 | 设置: 4种 (H1_M25, H1_M21, H2_M25, H2_M21)</p>
    
    <div class="summary">
        <h2>📊 测试概述</h2>
        <p><strong>模板类型:</strong></p>
        <ul>
            <li><strong>H1 (清单型):</strong> 3-field method_variants, A/B/C checklist idea</li>
            <li><strong>H2 (判断型):</strong> 2-field method_variants with insight framing, solid检验 3-question filter</li>
        </ul>
        <p><strong>模型:</strong> M25 (minimaxm25, ~2min/paper) vs M21 (minimaxm21, ~1.5min/paper)</p>
    </div>
'''
    
    # 逐论文展示
    for paper in PAPERS:
        html += f'''
    <div class="paper-card">
        <h2>📄 {PAPER_TITLES.get(paper, paper)}</h2>
        <p><code>{paper}</code></p>
        
        <div class="setting-grid">
'''
        
        for setting in SETTINGS:
            data = all_data[setting].get(paper)
            if not data:
                html += f'''
            <div class="setting-box {setting[:2].lower()}">
                <h3>{setting}</h3>
                <p style="color: red;">❌ 数据加载失败</p>
            </div>
'''
                continue
            
            # 评估各字段
            oneliner_score, oneliner_issues = evaluate_oneliner(data.get('cn_oneliner', ''))
            abstract_score, abstract_issues = evaluate_abstract(data.get('cn_abstract', ''))
            editorial_score, editorial_issues = evaluate_editorial(data.get('editorial_note', ''))
            variants_score, variants_issues = evaluate_method_variants(data.get('method_variants', []))
            cite_score, cite_issues = evaluate_core_cite(data.get('core_cite', []), [])
            idea_score, idea_issues = evaluate_idea(data.get('idea', []))
            
            total_score = oneliner_score + abstract_score + editorial_score + variants_score + cite_score + idea_score
            max_score = 30
            
            score_class = 'high' if total_score >= 25 else ('medium' if total_score >= 20 else 'low')
            
            html += f'''
            <div class="setting-box {setting[:2].lower()}">
                <h3>{setting}</h3>
                <p class="score {score_class}">总分: {total_score}/{max_score}</p>
                <table style="font-size: 0.85em;">
                    <tr><td class="field-label">cn_oneliner</td><td>{oneliner_score}/5 {'✅' if oneliner_score >= 4 else '⚠️'}</td></tr>
                    <tr><td class="field-label">cn_abstract</td><td>{abstract_score}/5 {'✅' if abstract_score >= 4 else '⚠️'}</td></tr>
                    <tr><td class="field-label">editorial_note</td><td>{editorial_score}/5 {'✅' if editorial_score >= 4 else '⚠️'}</td></tr>
                    <tr><td class="field-label">method_variants</td><td>{variants_score}/5 {'✅' if variants_score >= 4 else '⚠️'}</td></tr>
                    <tr><td class="field-label">core_cite</td><td>{cite_score}/5 {'✅' if cite_score >= 4 else '⚠️'}</td></tr>
                    <tr><td class="field-label">idea</td><td>{idea_score}/5 {'✅' if idea_score >= 4 else '⚠️'}</td></tr>
                </table>
'''
            
            # 显示关键问题
            all_issues = oneliner_issues + abstract_issues + editorial_issues + variants_issues + cite_issues + idea_issues
            if all_issues:
                html += '<details><summary style="cursor: pointer; color: #666;">查看问题详情</summary><ul style="font-size: 0.8em; color: #666;">'
                for issue in all_issues[:5]:  # 最多显示5个问题
                    html += f'<li>{issue}</li>'
                html += '</ul></details>'
            
            html += '</div>'
        
        html += '</div></div>'
    
    # 汇总对比
    html += '''
    <div class="summary">
        <h2>📈 汇总对比</h2>
        <table>
            <tr>
                <th>设置</th>
                <th>H1 vs H2 特点</th>
                <th>建议</th>
            </tr>
            <tr>
                <td><strong>H1_M25</strong></td>
                <td>清单型模板，minimaxm25模型</td>
                <td>稳定输出，格式规范</td>
            </tr>
            <tr>
                <td><strong>H1_M21</strong></td>
                <td>清单型模板，minimaxm21模型</td>
                <td>速度快，但可能略简化</td>
            </tr>
            <tr>
                <td><strong>H2_M25</strong></td>
                <td>判断型模板，minimaxm25模型</td>
                <td>idea深度更好</td>
            </tr>
            <tr>
                <td><strong>H2_M21</strong></td>
                <td>判断型模板，minimaxm21模型</td>
                <td>速度与质量的平衡</td>
            </tr>
        </table>
    </div>
'''
    
    html += '''
</body>
</html>
'''
    
    # 写入文件
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f'✅ HTML 报告已生成: {OUTPUT_FILE}')
    return OUTPUT_FILE

if __name__ == '__main__':
    generate_html()
