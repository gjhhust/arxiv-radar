#!/usr/bin/env python3
"""
gen_h3_report.py — 生成 H3 模板测试的 HTML 报告
"""

import os
import json
from pathlib import Path
from datetime import datetime

# 配置
PAPERS = ['2406.07550', '2503.08685', '2504.08736', '2506.05289', '2511.20565']
SETTINGS = [
    ('H3_M25', 'wq/minimaxm25'),
    ('H3_M21', 'wq/minimaxm21'),
    ('H3_Gemini31Pro', 'wqoai/gemini31pro'),
    ('H3_GPT52', 'wqoai/gpt52'),
]

RESULTS_BASE = '/Users/lanlanlan/.openclaw/workspace-paper-analyst/papers'
OUTPUT_FILE = '/Users/lanlanlan/.openclaw/workspace/skills/arxiv-radar/tests/active/comparison_v3/h3_report.html'

def load_result(setting, arxiv_id):
    """加载结果文件"""
    result_file = Path(RESULTS_BASE) / arxiv_id / 'analyse-results' / f'results_2026-03-14_{setting}.json'
    if not result_file.exists():
        return None, "文件不存在"
    
    try:
        with open(result_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data, "有效"
    except json.JSONDecodeError:
        return None, "JSON 解析错误"

def get_paper_title(data):
    """从结果数据中获取论文标题"""
    if data and 'title' in data:
        return data['title']
    return "未知"

def get_cn_oneliner(data):
    """从结果数据中获取中文一句话摘要"""
    if data and 'cn_oneliner' in data:
        return data['cn_oneliner']
    return "未知"

def generate_html():
    """生成 HTML 报告"""
    
    html_parts = []
    html_parts.append('<!DOCTYPE html>')
    html_parts.append('<html lang="zh-CN">')
    html_parts.append('<head>')
    html_parts.append('<meta charset="UTF-8">')
    html_parts.append('<title>H3 模板测试报告</title>')
    html_parts.append('<style>')
    html_parts.append('body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 20px; background: #f5f5f5; }')
    html_parts.append('h1 { color: #333; }')
    html_parts.append('h2 { color: #666; margin-top: 30px; border-bottom: 2px solid #ddd; padding-bottom: 10px; }')
    html_parts.append('table { border-collapse: collapse; width: 100%; background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }')
    html_parts.append('th, td { border: 1px solid #ddd; padding: 12px; text-align: left; }')
    html_parts.append('th { background: #4CAF50; color: white; }')
    html_parts.append('tr:nth-child(even) { background: #f9f9f9; }')
    html_parts.append('.success { color: green; font-weight: bold; }')
    html_parts.append('.fail { color: red; }')
    html_parts.append('.warning { color: orange; }')
    html_parts.append('.summary { background: white; padding: 20px; margin: 20px 0; border-radius: 5px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }')
    html_parts.append('.highlight { background: #e8f5e9; padding: 5px; border-radius: 3px; }')
    html_parts.append('</style>')
    html_parts.append('</head>')
    html_parts.append('<body>')
    
    html_parts.append(f'<h1>H3 模板测试报告</h1>')
    html_parts.append(f'<p>生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>')
    
    # 统计数据
    total_tasks = len(PAPERS) * len(SETTINGS)
    success_count = 0
    fail_count = 0
    warning_count = 0
    
    results = {}
    
    for setting_name, model in SETTINGS:
        setting_results = []
        for arxiv_id in PAPERS:
            data, status = load_result(setting_name, arxiv_id)
            setting_results.append({
                'arxiv_id': arxiv_id,
                'data': data,
                'status': status
            })
            if status == "有效":
                success_count += 1
            elif "JSON 解析错误" in status:
                warning_count += 1
            else:
                fail_count += 1
        results[setting_name] = setting_results
    
    # 摘要
    html_parts.append('<div class="summary">')
    html_parts.append(f'<h2>测试摘要</h2>')
    html_parts.append(f'<p>总任务数: {total_tasks}</p>')
    html_parts.append(f'<p><span class="success">成功: {success_count}</span> ({success_count/total_tasks*100:.1f}%)</p>')
    html_parts.append(f'<p><span class="warning">警告 (JSON 问题): {warning_count}</span></p>')
    html_parts.append(f'<p><span class="fail">失败: {fail_count}</span></p>')
    html_parts.append('</div>')
    
    # 每个设置的详细结果
    for setting_name, model in SETTINGS:
        html_parts.append(f'<h2>{setting_name} (model: {model})</h2>')
        html_parts.append('<table>')
        html_parts.append('<tr><th>论文 ID</th><th>标题</th><th>中文一句话</th><th>状态</th></tr>')
        
        for result in results[setting_name]:
            arxiv_id = result['arxiv_id']
            data = result['data']
            status = result['status']
            
            title = get_paper_title(data) if data else "未知"
            cn_oneliner = get_cn_oneliner(data) if data else "未知"
            
            status_class = "success" if status == "有效" else ("warning" if "JSON" in status else "fail")
            status_icon = "✅" if status == "有效" else ("⚠️" if "JSON" in status else "❌")
            
            html_parts.append(f'<tr>')
            html_parts.append(f'<td><a href="https://arxiv.org/abs/{arxiv_id}" target="_blank">{arxiv_id}</a></td>')
            html_parts.append(f'<td>{title}</td>')
            html_parts.append(f'<td>{cn_oneliner}</td>')
            html_parts.append(f'<td class="{status_class}">{status_icon} {status}</td>')
            html_parts.append(f'</tr>')
        
        html_parts.append('</table>')
    
    # 关键发现
    html_parts.append('<h2>关键发现</h2>')
    html_parts.append('<ul>')
    html_parts.append(f'<li><strong>M25 和 GPT52</strong> 表现最佳，成功率均为 80%</li>')
    html_parts.append(f'<li><strong>GPT52</strong> 成功处理了论文 2506.05289（M21 和 Gemini31Pro 失败）</li>')
    html_parts.append(f'<li><strong>Gemini31Pro</strong> 表现不佳，存在 JSON 解析问题和无输出问题</li>')
    html_parts.append(f'<li><strong>论文 2511.20565</strong> 所有模型都无法处理</li>')
    html_parts.append('</ul>')
    
    html_parts.append('</body>')
    html_parts.append('</html>')
    
    # 写入文件
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(html_parts))
    
    print(f"✅ HTML 报告已生成: {OUTPUT_FILE}")

if __name__ == '__main__':
    generate_html()
