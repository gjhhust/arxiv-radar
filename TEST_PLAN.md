# arxiv-radar TEST_PLAN.md

_最后更新：2026-03-14_

---

## 一、已完成里程碑

| 阶段 | 内容 | 结论 | 日期 |
|------|------|------|------|
| Prompt A/B/C | 单调用 vs 双调用、字段设计 | Scheme B（单调用）优于 A | 2026-03-12 |
| Variant D/E/F | editorial_note 格式、core_cite 结构 | Variant F 定稿为生产 prompt | 2026-03-12 |
| E2E Test v2 | 11 papers × 3 models，33 cells | minimaxm25 稳定、gpt52 引用最佳 | 2026-03-12 |
| G/H Test v1 | 有无 bib_mapping 对引用幻觉率影响 | 结论无效（bib 缺失导致误报，约 58%） | 2026-03-13 |
| G/H Test v2 | G（无锚点）vs H（有 S2 锚点+核查） | **H=100%，G=69%，S2 锚点有实质效果** | 2026-03-14 |
| paper-analyst agent | 独立 agent，arxiv-fetch skill | Phase 0 完成，agent 可正常调用 | 2026-03-13 |
| H vs I-a（公平对比） | 同 agent，唯一变量为引用列表获取方式 | **H=100%，I-a=65%，H 方案确定** | 2026-03-14 |
| H1 vs H2 预跑 | 清单型 vs 判断型 prompt，11×2=22 sessions | H1 cite=93%, H2 cite=89%（旧路径，轮廓结论） | 2026-03-14 |

---

## 二、当前锚定状态（Phase 1.5 — H1 vs H2 正式对比）

### 测试目标

**H vs I-a 已确定 H 方案。现对 H 方案的 prompt 质量做 A/B 测试：**

| 维度 | H1（清单型） | H2（判断型） |
|------|------------|------------|
| method_variants | 3 字段（base_method/variant_tag/description） | 2 字段（variant_tag/description，insight 视角） |
| idea 过滤 | A/B/C 清单分类，禁止列表 | solid-检验 三问（新假设？为何没做？不平凡？） |
| core_cite 排序 | extends→uses→contrasts→supports→mentions | "故事线"排序 |

### 测试执行协议（clean run）

```
Phase 1A — H1 方案
  1. Mox 依次 spawn paper-analyst sessions（rolling ≤5）
  2. 等待全部 11 篇完成
  3. 自动 spawn 清理子 agent：
     - 复制 papers/{id}/analyse-results/results_*_H1.json → tests/.../results_H1/
     - 备份 paper-analyst memory/YYYY-MM-DD.md → results_H1/memory_backup.md
  4. ⚠️ 用户授权后：移动原文件出 analyse-results/，清除 memory 中 H1 条目
  5. Mox verify H1（写回 + 输出 retry 列表）

Phase 1B — H2 方案（紧接 1A 清理后）
  同上操作，结果存入 results_H2/

Phase 1C — 汇总报告
  python3 run_h1_h2.py --step report
```

> **隔离原则**：H1 结果移出后，H2 paper-analyst sessions 不会命中缓存，保证两轮分析相互独立。
> **操作日志**：每次改动记录到 `logs/YYYY-MM-DD.md`。

### 测试位置

```
tests/active/comparison_v3/
├── run_h1_h2.py              ← H1 vs H2 测试脚本（verify/report）
├── run_comparison_v2.py      ← H vs I-a 脚本（已存档用途）
├── tasks/                    ← 任务文件（H1_*.txt / H2_*.txt）
├── results_H1/               ← H1 结果副本 + memory 备份
│   └── memory_backup.md
├── results_H2/               ← H2 结果副本 + memory 备份
│   └── memory_backup.md
├── state_h1_h2.json          ← verify 结果
└── report_h1_h2.html         ← 最终对比报告
```

paper-analyst 结果写入路径：
```
~/.openclaw/workspace-paper-analyst/papers/{arxiv_id}/analyse-results/
  results_YYYYMMDD_H1.json   ← H1 分析 + Mox 验证字段（写回）
  results_YYYYMMDD_H2.json   ← H2 分析（H1 清理后才跑）
```

### Phase 1.5 任务清单

| Task | Status |
|------|--------|
| 路径统一为 `analyse-results/`（prompts + AGENTS.md + ARCHITECTURE.md） | ✅ 2026-03-14 |
| run_h1_h2.py：find_result 兼容新路径 + verify 写回 + retry 列表 | ✅ 2026-03-14 |
| logs/ 目录建立，操作日志规范 | ✅ 2026-03-14 |
| **H1 正式跑（11 sessions，新路径）** | ⬜ |
| 清理子 agent 备份 memory + 用户授权移出 H1 结果 | ⬜ |
| verify H1 | ⬜ |
| **H2 正式跑（11 sessions）** | ⬜ |
| 清理子 agent 备份 memory + 用户授权移出 H2 结果 | ⬜ |
| verify H2 | ⬜ |
| report_h1_h2.html 最终报告 | ⬜ |
| 选定 canonical template（H1 or H2） | ⬜ |

---

## 三、后续 Phase

### Phase 2 — 接入生产流程

- **前置条件**：H1 vs H2 得出明确结论，canonical template 确定
- 写 `post_process.py`：core_cite title → DB arxiv_id 匹配 → CORE_CITE 边写入
- 更新 `main.py` 调用 paper-analyst agent（sessions_spawn）

### Phase 3 — 知识图谱扩展

- 每日分析结果归档至 `papers/{arxiv_id}/analyse-results/results_YYYYMMDD.json`
- CORE_CITE 边入 DB，扩展论文关系图

---

## 四、关键常数

| 项目 | 值 |
|------|-----|
| 测试论文集 | 11 篇（ARXIV_IDS in run_h1_h2.py；2601.01535 为"Improving Flexible Image Tokenizers"，非 MAGI-1） |
| 验证相似度阈值 | ≥ 0.8（SequenceMatcher.ratio） |
| 重试阈值 | cite_rate < 0.6 |
| 默认模型 | wq/minimaxm25 |
| DB 路径 | `data/paper_network.db` |
| Prompt 存放 | `prompts/` |
| Canonical prompt（待定） | `prompts/prompt_H_template.txt`（H1 or H2 胜出后更新） |
| 操作日志 | `logs/YYYY-MM-DD.md` |

---

## 一、已完成里程碑

| 阶段 | 内容 | 结论 | 日期 |
|------|------|------|------|
| Prompt A/B/C | 单调用 vs 双调用、字段设计 | Scheme B（单调用）优于 A | 2026-03-12 |
| Variant D/E/F | editorial_note 格式、core_cite 结构 | Variant F 定稿为生产 prompt | 2026-03-12 |
| E2E Test v2 | 11 papers × 3 models，33 cells | minimaxm25 稳定、gpt52 引用最佳 | 2026-03-12 |
| G/H Test v1 | 有无 bib_mapping 对引用幻觉率影响 | 结论无效（bib 缺失导致误报，约 58%） | 2026-03-13 |
| G/H Test v2 | G（无锚点）vs H（有 S2 锚点+核查） | **H=100%，G=69%，S2 锚点有实质效果** | 2026-03-14 |
| paper-analyst agent | 独立 agent，arxiv-fetch skill | Phase 0 完成，agent 可正常调用 | 2026-03-13 |
| I-a Test（不公平） | agent 自查 vs 直接 LLM | 对比无效（输入/机制不一致） | 2026-03-14 |

---

## 二、当前锚定状态（Phase 1）

### 测试目标

**公平对比 H vs I-a**：控制所有变量，只改变引用列表的获取方式。

| 变量 | H | I-a |
|------|---|-----|
| 模型 | wq/minimaxm25 | wq/minimaxm25 |
| agent | paper-analyst | paper-analyst |
| 输入 | arxiv_id + title（无原文） | arxiv_id + title（无原文） |
| 字段描述 | 完全一致 | 完全一致 |
| 引用列表来源 | Python 预查询 → 嵌入 prompt | prompt 内 sqlite3 命令 → agent 执行 |
| SQL 查询 | 同一条（见下） | 同一条（见下） |

**SQL（单一数据源）：**
```sql
SELECT p.title, p.id FROM paper_edges e
JOIN papers p ON e.dst_id = p.id
WHERE e.src_id = '{arxiv_id}' AND e.edge_type = 'CITES'
ORDER BY p.title
```

### 测试位置

```
tests/active/comparison_v3/
├── run_comparison_v2.py   ← 主测试脚本（build/verify/report）
├── tasks/                 ← 生成的任务文件（H 和 I-a 各 11 篇）
│   └── manifest.json
├── results_H/             ← paper-analyst H 方案结果
├── results_Ia/            ← paper-analyst I-a 方案结果
├── state.json             ← 验证结果（--step verify 生成）
└── report.html            ← 最终对比报告（--step report 生成）
```

### 运行方式

```bash
# 步骤 1：生成任务文件（已完成）
python3 tests/active/comparison_v3/run_comparison_v2.py --step build

# 步骤 2：Mox 通过 sessions_spawn 调用 paper-analyst（22 个 session，11×H+11×I-a）
# 结果保存路径：papers/{arxiv_id}/results_20260314_H.json / _Ia.json

# 步骤 3：验证 + 生成报告
python3 tests/active/comparison_v3/run_comparison_v2.py --step verify
python3 tests/active/comparison_v3/run_comparison_v2.py --step report
```

### Phase 1 任务清单

| Task | Status |
|------|--------|
| 统一 prompt 模板（H/I-a 骨架一致，仅末尾不同） | ✅ 2026-03-14 |
| run_comparison_v2.py（build/verify/report） | ✅ 2026-03-14 |
| tasks/ 任务文件生成（11 篇 × 2 方案） | ✅ 2026-03-14 |
| paper-analyst sessions 执行（22 个 session） | ⬜ |
| verify：S2 相似度验证 → state.json | ⬜ |
| report：HTML 对比报告（含完整字段 + 逐 cite 分数） | ⬜ |
| 选定方案 → 写入生产流程 | ⬜ |

---

## 三、后续 Phase

### Phase 2 — 接入生产流程

- **前置条件**：Phase 1 得出 H vs I-a 明确结论
- 将选定方案的 prompt 更新到 `scripts/paper_analyst.py`
- 更新 `scripts/main.py` 调用 paper-analyst agent（sessions_spawn）
- 每日分析结果归档到 `papers/{arxiv_id}/results_YYYYMMDD.json`

### Phase 3 — 知识图谱扩展

- 写 `scripts/post_process.py`：core_cite title → DB arxiv_id 匹配 → CORE_CITE 边写入
- 更新 `ARCHITECTURE.md` 数据流图

---

## 四、关键常数

| 项目 | 值 |
|------|-----|
| 测试论文集 | 11 篇（见 run_comparison_v2.py ARXIV_IDS） |
| 验证相似度阈值 | ≥ 0.8（SequenceMatcher.ratio） |
| 默认模型 | wq/minimaxm25 |
| DB 路径 | `data/paper_network.db` |
| Prompt 存放 | `prompts/` |
| 生产 prompt | `prompts/variant_f_production.md` |
