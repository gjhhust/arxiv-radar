#!/usr/bin/env python3
"""
gen_h3_tasks.py — 生成 H3 模板的测试任务文件

4 个 setting：H3_M25, H3_M21, H3_Gemini31Pro, H3_GPT52
5 篇文章：2406.07550, 2503.08685, 2504.08736, 2506.05289, 2511.20565

模板占位符：
- {arxiv_id}: 论文 ID
- {paper_title}: 论文标题
- {s2_ref_list}: Semantic Scholar 引用列表
- {scheme}: 分析方案名称（如 H3_M25）
- {date_md}: 日期（YYYY-MM-DD 格式，用于文件名）
- {timestamp}: 完整时间戳（YYYY-MM-DD HH:MM:SS 格式，用于 memory 记录）
- {sub_agent_workspace}: 子代理工作目录（默认为 workspace-paper-analyst）
"""

import os
from pathlib import Path
from datetime import datetime

# 配置
PAPERS = {
    '2406.07550': 'An Image is Worth 32 Tokens for Reconstruction and Generation',
    '2503.08685': '"Principal Components" Enable A New Language of Images',
    '2504.08736': 'GigaTok: Scaling Visual Tokenizers to 3 Billion Parameters for Autoregressive Image Generation',
    '2506.05289': 'AliTok: Towards Sequence Modeling Alignment between Tokenizer and Autoregressive Model',
    '2511.20565': 'DINO-Tok: Adapting DINO for Visual Tokenizers',
}

SETTINGS = ['H3_M25', 'H3_M21', 'H3_Gemini31Pro', 'H3_GPT52']

# 路径
TEMPLATE_PATH = '/Users/lanlanlan/.openclaw/workspace/skills/arxiv-radar/prompts/prompt_H3_template.txt'
ARCHIVE_TASKS_PATH = '/Users/lanlanlan/.openclaw/workspace/skills/arxiv-radar/tests/archive/04_h1_h2_model_ab/comparison_v3/tasks'
OUTPUT_PATH = '/Users/lanlanlan/.openclaw/workspace/skills/arxiv-radar/tests/active/comparison_v3/tasks'

# 子代理工作目录（默认为 paper-analyst 的工作目录）
SUB_AGENT_WORKSPACE = '/Users/lanlanlan/.openclaw/workspace-paper-analyst'

def read_template():
    """读取 H3 模板"""
    with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
        return f.read()

def extract_ref_list(arxiv_id):
    """从归档任务文件中提取引用列表"""
    # 尝试读取归档的 H1 任务文件
    archive_file = Path(ARCHIVE_TASKS_PATH) / f'H1_{arxiv_id}.txt'
    if not archive_file.exists():
        print(f"⚠️ 归档文件不存在: {archive_file}")
        return ""
    
    with open(archive_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 提取参考列表部分（从 "**参考列表" 开始到 "**保存要求" 结束）
    start_marker = "**参考列表（core_cite 须来自其中，输出前逐条核查，列表中找不到则删除）：**"
    end_marker = "**保存要求（必须执行，不得跳过）**"
    
    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)
    
    if start_idx == -1 or end_idx == -1:
        print(f"⚠️ 无法提取参考列表: {arxiv_id}")
        return ""
    
    ref_list = content[start_idx + len(start_marker):end_idx].strip()
    return ref_list

def generate_task(template, arxiv_id, paper_title, ref_list, setting, date_md, timestamp, sub_agent_workspace):
    """生成单个任务文件"""
    # 替换占位符
    task = template.replace('{arxiv_id}', arxiv_id)
    task = task.replace('{paper_title}', paper_title)
    task = task.replace('{s2_ref_list}', ref_list)
    task = task.replace('{scheme}', setting)
    task = task.replace('{date_md}', date_md)
    task = task.replace('{timestamp}', timestamp)
    task = task.replace('{sub_agent_workspace}', sub_agent_workspace)
    
    return task

def main():
    # 读取模板
    template = read_template()
    print(f"✅ 读取 H3 模板: {len(template)} bytes")
    
    # 创建输出目录
    os.makedirs(OUTPUT_PATH, exist_ok=True)
    
    # 生成日期和时间戳
    now = datetime.now()
    date_md = now.strftime('%Y-%m-%d')  # YYYY-MM-DD（用于文件名）
    timestamp = now.strftime('%Y-%m-%d %H:%M:%S')  # YYYY-MM-DD HH:MM:SS（用于 memory 记录）
    print(f"📅 日期: {date_md}")
    print(f"⏰ 时间戳: {timestamp}")
    print(f"📁 子代理工作目录: {SUB_AGENT_WORKSPACE}")
    
    # 为每个 paper 和 setting 生成任务文件
    total = 0
    for arxiv_id, paper_title in PAPERS.items():
        # 提取引用列表
        ref_list = extract_ref_list(arxiv_id)
        print(f"📄 {arxiv_id}: 提取了 {len(ref_list.split(chr(10)))} 行引用")
        
        for setting in SETTINGS:
            # 生成任务内容
            task = generate_task(template, arxiv_id, paper_title, ref_list, setting, date_md, timestamp, SUB_AGENT_WORKSPACE)
            
            # 写入文件
            output_file = Path(OUTPUT_PATH) / f'{setting}_{arxiv_id}.txt'
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(task)
            
            total += 1
            print(f"  ✅ {setting}_{arxiv_id}.txt")
    
    print(f"\n🎉 共生成 {total} 个任务文件")
    print(f"   - 工作目录: {SUB_AGENT_WORKSPACE}/papers/{{arxiv_id}}")
    print(f"   - memory 文件: {SUB_AGENT_WORKSPACE}/memory/{date_md}.md")
    print(f"   - results 文件: results_{date_md}_{{scheme}}.json")

if __name__ == '__main__':
    main()
