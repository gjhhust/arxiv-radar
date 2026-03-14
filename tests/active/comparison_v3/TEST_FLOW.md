# H3 模板测试流程文档

## 测试目标
- 测试 H3 模板在 4 个模型上的表现
- 模型：minimaxm25, minimaxm21, gemini31pro, gpt52
- 文章：2406.07550, 2503.08685, 2504.08736, 2506.05289, 2511.20565
- 共 20 个任务

## ⚠️ 关键注意事项

### 1. 串行运行，防止记忆污染
- **每个任务必须串行运行**，不能并发
- paper-analyst agent 会保留记忆，并发运行会导致交叉污染
- 等待一个任务完全完成后再运行下一个

### 2. 🚨 绝对禁止简化任务文件内容
- **P0 规则**：spawn 时必须传递完整任务文件内容，绝对禁止手动精简
- 使用 exec 读取完整任务文件内容，然后传递给 spawn
- 不删除任何示例、字段描述、参考列表

### 3. 每次运行前校验
- 检查任务文件是否存在
- 检查任务文件内容是否正确（arxiv_id, paper_title, save_path）
- 检查结果文件保存路径是否正确

### 4. 记录测试时间
- 每个 setting 使用不同的模型
- 记录每个任务的开始时间、结束时间、耗时

### 5. 结果检查
- 检查 JSON 文件是否有效
- 检查保存路径是否正确
- 如果结果文件不存在，从任务完成事件中提取 JSON 并保存

## 测试配置

| Setting | 模型 | 任务文件前缀 |
|---------|------|-------------|
| H3_M25 | wq/minimaxm25 | H3_M25_*.txt |
| H3_M21 | wq/minimaxm21 | H3_M21_*.txt |
| H3_Gemini31Pro | wqoai/gemini31pro | H3_Gemini31Pro_*.txt |
| H3_GPT52 | wqoai/gpt52 | H3_GPT52_*.txt |

**注意**：GPT 和 Gemini 模型使用 `wqoai/` 前缀，不是 `wq/`

## 🔧 固定 Spawn 流程（严格执行）

```
1. 校验任务文件：
   - 检查文件存在
   - 检查 arxiv_id, paper_title, save_path 正确

2. 读取完整任务文件内容：
   exec: cat 任务文件路径
   - 不进行任何简化或修改

3. Spawn paper-analyst agent：
   sessions_spawn:
   - agentId: paper-analyst
   - runtime: subagent
   - mode: run
   - model: 根据 setting 选择
   - task: 完整的任务文件内容（从 step 2 获取）
   - timeoutSeconds: 300

4. 等待任务完成：
   sessions_yield

5. 检查结果文件：
   - 验证 JSON 文件存在且有效
   - 如果不存在，从任务完成事件中提取 JSON 并保存

6. 记录时间：
   - 记录开始时间、结束时间、耗时

7. 继续下一个任务
```

## 测试顺序

### S1: H3_M25 (minimaxm25)
1. H3_M25_2406.07550
2. H3_M25_2503.08685
3. H3_M25_2504.08736
4. H3_M25_2506.05289
5. H3_M25_2511.20565

### S2: H3_M21 (minimaxm21)
1. H3_M21_2406.07550
2. H3_M21_2503.08685
3. H3_M21_2504.08736
4. H3_M21_2506.05289
5. H3_M21_2511.20565

### S3: H3_Gemini31Pro (gemini31pro)
1. H3_Gemini31Pro_2406.07550
2. H3_Gemini31Pro_2503.08685
3. H3_Gemini31Pro_2504.08736
4. H3_Gemini31Pro_2506.05289
5. H3_Gemini31Pro_2511.20565

### S4: H3_GPT52 (gpt52)
1. H3_GPT52_2406.07550
2. H3_GPT52_2503.08685
3. H3_GPT52_2504.08736
4. H3_GPT52_2506.05289
5. H3_GPT52_2511.20565

## spawn 参数

```
agentId: paper-analyst
runtime: subagent
mode: run
model: 根据 setting 选择（注意 wq/ vs wqoai/）
task: 完整的任务文件内容（绝对禁止简化）
timeoutSeconds: 300
```

## 结果保存路径

```
/Users/lanlanlan/.openclaw/workspace-paper-analyst/papers/{arxiv_id}/analyse-results/results_20260314_{setting}.json
```

## 当前状态

- [x] 记录错误到 error-log.md
- [x] 清理之前的测试结果
- [ ] 校验任务文件
- [ ] 运行 S1 (H3_M25)
- [ ] 运行 S2 (H3_M21)
- [ ] 运行 S3 (H3_Gemini31Pro)
- [ ] 运行 S4 (H3_GPT52)
- [ ] 生成 HTML 报告

---

## 🚨 2026-03-14 17:45 — H3 模板修复记录

### 问题

H3 模板测试中发现 paper-agent **没有写入 memory 记录**（12/20 成功但 memory 条目为 0）。

### 根因

H3 模板"保存要求"部分**缺少 memory 写入步骤**（H1 有，H3 没有）。

### 修复

1. **模板文件** `prompts/prompt_H3_template.txt`：
   - 添加步骤 3：追加内容到 `memory/{date_md}.md`
   - 使用 `{date_md}`（YYYY-MM-DD）和 `{date_compact}`（YYYYMMDD）两种日期格式

2. **生成脚本** `gen_h3_tasks.py`：
   - 自动生成 `date_md` 和 `date_compact`
   - 替换模板中的占位符

### 验证

重新生成 20 个 H3 任务文件，验证 memory 写入步骤存在：
```
3. 在写入 JSON 成功后，追加以下内容到 `memory/2026-03-14.md`：
```
## {arxiv_id} | {paper_title}
- date: 2026-03-14
- task_summary: {scheme} 结构化分析
- result: /Users/lanlanlan/.../results_20260314_{scheme}.json
```
```

### 教训

> **模板创建时必须与已有模板对齐保存要求步骤**，避免遗漏关键指令。

---

## 2026-03-14 18:05 — 日期占位符统一

### 修改

用户要求将 `date_compact` 和 `date_md` 统一成 `date_md`。

1. **H3 模板**：所有日期占位符统一为 `{date_md}`
2. **gen_h3_tasks.py**：删除 `date_compact` 变量，生成时自动转换 results 文件名格式

### 日期格式处理

| 用途 | 格式 | 示例 |
|------|------|------|
| 模板占位符 | `{date_md}` | `{date_md}` |
| results 文件名 | YYYYMMDD（自动转换） | `results_20260314_H3_M25.json` |
| memory 文件名 | YYYY-MM-DD | `memory/2026-03-14.md` |

### 用户微调

用户对 H3 模板语言表达进行了微调：
- 步骤 2 描述更自然："立即追加以下内容到你的今天记忆目录内归档"
- 补充 `task_summary` 字段内容：`{scheme} 结构化分析`

---

## 2026-03-14 18:08 — 日期格式完全统一

用户要求 results 文件名也使用 YYYY-MM-DD 格式，与 memory 文件名格式统一。

### 最终日期格式

| 用途 | 格式 | 示例 |
|------|------|------|
| 模板占位符 | `{date_md}` | `{date_md}` |
| results 文件名 | YYYY-MM-DD | `results_2026-03-14_H3_M25.json` |
| memory 文件名 | YYYY-MM-DD | `memory/2026-03-14.md` |

---

## 2026-03-14 18:12 — 全面核查 + 模板统一 + 老文件归档

### 模板统一

所有模板文件的日期占位符统一为 `{date_md}`：

| 模板 | 修改 |
|------|------|
| H1 | `{date}` → `{date_md}` |
| H2 | `{date}` → `{date_md}` |
| H | 硬编码 `20260314_H` → `{date_md}_{scheme}` |
| Ia | 硬编码 `20260314_Ia` → `{date_md}_{scheme}` |
| Ib | `{date}` → `{date_md}` |

### 老文件归档

- 归档目录：`workspace-paper-analyst/results_archive_YYYYMMDD/`
- 归档内容：老命名格式 results 文件（YYYYMMDD 格式）
- 剩余老命名格式文件：0 个
