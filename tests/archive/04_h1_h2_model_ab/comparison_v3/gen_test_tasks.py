#!/usr/bin/env python3
"""
gen_test_tasks.py — 从原始任务文件生成 model 变体任务文件

用法:
  python3 gen_test_tasks.py --scheme H1 --model M25 --date 20260314
  python3 gen_test_tasks.py --scheme H2 --model M21 --date 20260314 --papers 2406.07550,2503.08685

设计原则:
  - 原始任务文件（H1_2503.08685.txt）是 source of truth
  - 原始文件已有完整引用列表（来自S2数据库，50+条）和正确prompt
  - 脚本只做路径替换，绝不修改prompt内容
  - 输出文件保持原始完整性，只改保存路径的model后缀

输入: tasks/H1_2503.08685.txt（原始任务，含完整引用）
输出: tasks/20260314_H1_M25_2503.08685.txt（路径替换后的变体）
"""

import argparse
import re
from pathlib import Path

# ── 路径配置 ───────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
TASKS_DIR = SCRIPT_DIR / 'tasks'
PAPER_ANALYST_WORKSPACE = '/Users/lanlanlan/.openclaw/workspace-paper-analyst'

# ── 模型显示名映射 ─────────────────────────────────────────
MODEL_DISPLAY = {
    'M25': 'minimaxm25',
    'M21': 'minimaxm21',
}

# ── 5篇测试论文 ────────────────────────────────────────────
TEST_PAPERS = ['2406.07550', '2503.08685', '2504.08736', '2506.05289', '2511.20565']


def transform_task(original_text, arxiv_id, date, scheme, model):
    """
    从原始任务文件生成model变体
    
    替换规则:
    1. results_20260314_H1.json → results_20260314_H1_M25.json
    2. memory/2026-03-14.md → /Users/lanlanlan/.openclaw/workspace-paper-analyst/memory/2026-03-14.md
    """
    new_scheme_tag = '{}_{}'.format(scheme, model)
    
    # 替换结果文件名: results_YYYYMMDD_H1.json → results_YYYYMMDD_H1_M25.json
    # 匹配: results_20260314_H1.json 或 results_20260314_H2.json
    pattern = r'results_{}_\w+.json'.format(date)
    
    def replace_result_path(match):
        old_path = match.group(0)
        # 提取 results_20260314_H1.json
        # 替换为 results_20260314_H1_M25.json
        return old_path.replace('.json', '_{}.json'.format(model))
    
    transformed = re.sub(pattern, replace_result_path, original_text)
    
    # 替换 memory 路径为绝对路径
    # memory/2026-03-14.md → /Users/lanlanlan/.openclaw/workspace-paper-analyst/memory/2026-03-14.md
    memory_date = '{}-{}-{}'.format(date[:4], date[4:6], date[6:8])
    
    # 匹配 `memory/2026-03-14.md` 或 memory/2026-03-14.md（不带反引号）
    memory_pattern = r'`?memory/\d{4}-\d{2}-\d{2}\.md`?'
    absolute_memory_path = '`{}/memory/{}.md`'.format(PAPER_ANALYST_WORKSPACE, memory_date)
    
    transformed = re.sub(memory_pattern, absolute_memory_path, transformed)
    
    return transformed


def main():
    parser = argparse.ArgumentParser(description='从原始任务文件生成model变体任务文件')
    parser.add_argument('--scheme', required=True, choices=['H1', 'H2'], help='分析方案')
    parser.add_argument('--model', required=True, choices=['M25', 'M21'], help='模型标识')
    parser.add_argument('--date', required=True, help='日期 YYYYMMDD')
    parser.add_argument('--papers', default='', help='论文 ID 列表（逗号分隔），默认5篇测试论文')
    args = parser.parse_args()

    model_name = MODEL_DISPLAY[args.model]
    
    # 确定要处理的论文列表
    if args.papers:
        paper_ids = [p.strip() for p in args.papers.split(',') if p.strip()]
    else:
        paper_ids = TEST_PAPERS
    
    # 确保任务目录存在
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    
    generated = []
    for arxiv_id in paper_ids:
        # 读取原始任务文件
        original_file = TASKS_DIR / '{}_{}.txt'.format(args.scheme, arxiv_id)
        if not original_file.exists():
            print('⚠️  原始任务文件不存在: {}，跳过'.format(original_file.name))
            continue
        
        original_text = original_file.read_text(encoding='utf-8')
        
        # 转换任务文件
        transformed_text = transform_task(
            original_text, arxiv_id, args.date, args.scheme, args.model)
        
        # 写入新文件
        out_file = TASKS_DIR / '{}_{}_{}_{}.txt'.format(
            args.date, args.scheme, args.model, arxiv_id)
        out_file.write_text(transformed_text, encoding='utf-8')
        
        print('  → {} ({} chars, refs from original)'.format(
            out_file.name, len(transformed_text)))
        generated.append(str(out_file))
    
    print('\n✅ 生成完成: {} 个任务文件 | scheme={} model={}'.format(
        len(generated), args.scheme, model_name))
    print('原始文件: tasks/{}_{{arxiv_id}}.txt'.format(args.scheme))
    print('输出文件: tasks/{}_{}_{{arxiv_id}}.txt'.format(args.date, args.scheme, args.model))
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
