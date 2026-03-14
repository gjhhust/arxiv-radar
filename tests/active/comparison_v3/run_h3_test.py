#!/usr/bin/env python3
"""
run_h3_test.py — 运行 H3 模板测试

使用 sessions_spawn 运行 paper-analyst agent，记录测试时间。
支持 transcript 路径追踪：自动查询 sessions.json 获取 transcript 路径并写入结果 JSON。

生产环境说明：
- 包含 JSON 容错处理，自动修复 LLM 生成的未转义双引号问题
- 适用于 GLM5 等 LLM 生成的 JSON 文件解析
"""

import os
import json
import re
import shutil
import time
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

TASKS_PATH = '/Users/lanlanlan/.openclaw/workspace/skills/arxiv-radar/tests/active/comparison_v3/tasks'
RESULTS_BASE = '/Users/lanlanlan/.openclaw/workspace-paper-analyst/papers'
TIMING_FILE = '/Users/lanlanlan/.openclaw/workspace/skills/arxiv-radar/tests/active/comparison_v3/test_timing.md'
SESSIONS_FILE = '/Users/lanlanlan/.openclaw/agents/paper-analyst/sessions/sessions.json'

def fix_json_llm_output(content):
    """
    修复 LLM 生成的 JSON 中常见的问题
    
    问题：GLM5 等 LLM 在生成 JSON 时，可能在字符串值内部使用未转义的双引号
    
    解决方案：
    1. 识别 JSON 字段模式：`"key": "value",` 或 `"key": "value"`
    2. 在字符串值内部，将未转义的双引号转义
    
    启发式规则：
    - JSON 字段的字符串值通常在一行内，以 ",\n" 或 "\n" 结尾
    - 字符串值内部的双引号通常是成对出现的中文引号或其他上下文
    - 字符串开始的双引号后面通常跟着文本内容
    - 字符串结束的双引号前面通常是文本内容，后面跟着 , 或 } 或换行
    
    生产环境说明：
    - 此函数用于处理 GLM5 等 LLM 生成的 JSON
    - 建议在所有读取 JSON 结果的脚本中使用此函数
    """
    
    # 分行处理
    lines = content.split('\n')
    fixed_lines = []
    
    for line in lines:
        # 检查是否是 JSON 字段行（包含 ": " 模式）
        if '": "' in line:
            # 使用正则提取字段
            match = re.match(r'^(\s*"[^"]+"\s*:\s*")(.*)$', line)
            if match:
                prefix = match.group(1)  #     "key": "
                rest = match.group(2)    # value", 或 value"
                
                # 找到字符串值的结束位置
                if rest.rstrip().endswith('",'):
                    value = rest[:-2]
                    suffix = '",'
                elif rest.rstrip().endswith('"'):
                    value = rest[:-1]
                    suffix = '"'
                else:
                    fixed_lines.append(line)
                    continue
                
                # 在 value 内部，将未转义的双引号转义
                fixed_value = re.sub(r'(?<!\\)"', r'\\"', value)
                
                # 重新构建行
                fixed_line = prefix + fixed_value + suffix
                fixed_lines.append(fixed_line)
            else:
                fixed_lines.append(line)
        else:
            fixed_lines.append(line)
    
    return '\n'.join(fixed_lines)

def safe_load_json(file_path):
    """
    安全加载 JSON 文件，自动修复常见问题
    
    生产环境说明：
    - LLM（尤其是 GLM5）生成的 JSON 可能在字符串值中包含未转义的双引号
    - 此函数会尝试自动修复这些问题
    - 如果修复失败，返回 None
    
    使用示例：
    data = safe_load_json('results.json')
    if data:
        print(data['arxiv_id'])
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 尝试直接解析
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        # 尝试修复
        fixed_content = fix_json_llm_output(content)
        
        try:
            data = json.loads(fixed_content)
            
            # 保存修复后的文件
            backup_path = str(file_path) + '.original'
            shutil.copy(file_path, backup_path)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(fixed_content)
            
            return data
        except json.JSONDecodeError:
            return None

def read_task_file(setting, arxiv_id):
    """读取任务文件内容"""
    task_file = Path(TASKS_PATH) / f'{setting}_{arxiv_id}.txt'
    if not task_file.exists():
        print(f"❌ 任务文件不存在: {task_file}")
        return None
    with open(task_file, 'r', encoding='utf-8') as f:
        return f.read()

def check_result_file(setting, arxiv_id):
    """检查结果文件是否存在且有效（使用容错加载）"""
    # 使用 YYYY-MM-DD 日期格式
    date_str = datetime.now().strftime('%Y-%m-%d')
    result_file = Path(RESULTS_BASE) / arxiv_id / 'analyse-results' / f'results_{date_str}_{setting}.json'
    if not result_file.exists():
        return None, "文件不存在"
    
    # 使用容错加载（自动修复未转义双引号等问题）
    data = safe_load_json(result_file)
    if data:
        return result_file, "有效"
    else:
        return result_file, "JSON 解析失败（已尝试修复）"

def append_timing(content):
    """追加时间记录"""
    with open(TIMING_FILE, 'a', encoding='utf-8') as f:
        f.write(content + '\n')

def load_sessions():
    """加载 sessions.json"""
    sessions_file = Path(SESSIONS_FILE)
    if not sessions_file.exists():
        return {}
    with open(sessions_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def find_transcript_path(setting, arxiv_id, sessions):
    """根据 label 查找 transcript 路径"""
    label = f'{setting}_{arxiv_id}'
    for session_key, session_data in sessions.items():
        if session_data.get('label') == label:
            return session_data.get('sessionFile')
    return None

def add_transcript_to_result(result_file, transcript_path):
    """将 transcript 路径和 session 时间追加写入结果 JSON"""
    if not result_file or not transcript_path:
        return
    
    result_path = Path(result_file)
    if not result_path.exists():
        return
    
    try:
        with open(result_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 添加 transcript 路径
        data['transcript_path'] = transcript_path
        
        # 添加 session 时间（当前时间，精确到秒）
        from datetime import datetime
        data['session_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        with open(result_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        return True
    except Exception as e:
        print(f"  ⚠️ 写入 transcript 路径失败: {e}")
        return False

def main():
    """主函数 - 打印任务列表和时间，并添加 transcript 路径"""
    print("=== H3 测试任务列表 ===\n")
    
    # 加载 sessions.json
    sessions = load_sessions()
    print(f"📚 加载了 {len(sessions)} 个 session 记录\n")
    
    all_tasks = []
    
    for setting_name, model in SETTINGS:
        print(f"### {setting_name} (model: {model})")
        append_timing(f"\n### {setting_name} (model: {model})")
        
        for arxiv_id in PAPERS:
            task_content = read_task_file(setting_name, arxiv_id)
            if task_content:
                result_file, status = check_result_file(setting_name, arxiv_id)
                
                # 查找 transcript 路径
                transcript_path = find_transcript_path(setting_name, arxiv_id, sessions)
                
                # 如果结果文件存在，尝试添加 transcript 路径
                if result_file and transcript_path:
                    if add_transcript_to_result(result_file, transcript_path):
                        status += " + transcript"
                
                task_info = {
                    'setting': setting_name,
                    'model': model,
                    'arxiv_id': arxiv_id,
                    'task_file': f'{TASKS_PATH}/{setting_name}_{arxiv_id}.txt',
                    'result_file': str(result_file) if result_file else None,
                    'transcript_path': transcript_path,
                    'status': status,
                    'task_content': task_content
                }
                all_tasks.append(task_info)
                print(f"  - {arxiv_id}: {status}")
                if transcript_path:
                    print(f"    📄 transcript: {Path(transcript_path).name}")
        print()
    
    print(f"共 {len(all_tasks)} 个任务")
    
    # 保存任务列表
    tasks_json = '/Users/lanlanlan/.openclaw/workspace/skills/arxiv-radar/tests/active/comparison_v3/tasks_list.json'
    with open(tasks_json, 'w', encoding='utf-8') as f:
        json.dump([{
            'setting': t['setting'],
            'model': t['model'],
            'arxiv_id': t['arxiv_id'],
            'task_file': t['task_file'],
            'result_file': t['result_file'],
            'transcript_path': t['transcript_path'],
            'status': t['status']
        } for t in all_tasks], f, indent=2, ensure_ascii=False)
    
    print(f"任务列表已保存: {tasks_json}")
    
    return all_tasks

if __name__ == '__main__':
    main()
