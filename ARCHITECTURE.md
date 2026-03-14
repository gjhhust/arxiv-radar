# arxiv-radar 架构文档

> 开发者维护手册。任何 session 接手前必读此文件。

**最后更新**: 2026-03-11  
**代码量**: 5,972 行 Python / 18 模块  
**仓库**: https://github.com/gjhhust/arxiv-radar

---

## 一、系统概览

arxiv-radar 由三条独立但互连的 pipeline 组成：

```
┌─────────────────────────────────────────────────────────────────────┐
│                        CONFIG LAYER                                 │
│  config.template.md → config_parser.py → config dict               │
│  (领域定义 / VIP作者 / 阈值 / 输出路径 / embedding模型)             │
└────────────────────────────┬────────────────────────────────────────┘
                             │
     ┌───────────────────────┼───────────────────────┐
     ▼                       ▼                       ▼
┌─────────────┐    ┌──────────────────┐    ┌──────────────────┐
│ DAILY       │    │ PKN (Paper       │    │ REPORT           │
│ PIPELINE    │    │ Knowledge Net)   │    │ PIPELINE         │
│             │    │                  │    │                  │
│ main.py     │    │ init_graph.py    │    │ weekly.py        │
│ crawler     │───▶│ semantic_scholar │    │ monthly.py       │
│ labeler     │    │ paper_db         │◀───│ aggregator       │
│ filter      │    │ baseline_extractor   │ reporter       │
│ recommender │    │ reference_ranker │    │ trend            │
│ reporter    │    │ context_injector │    │ obsidian_writer  │
└─────────────┘    └──────────────────┘    └──────────────────┘
```

---

## 二、模块职责表

| 模块 | 职责 | 输入 | 输出 | 依赖 |
|------|------|------|------|------|
| **config_parser** | 解析 Markdown 配置 | config.md | config dict | 无 |
| **crawler** | arXiv API 爬取 | 日期+分类 | papers[] | 无 |
| **labeler** | VIP/组织/类型标签 | papers[] | papers[] with labels | 无 |
| **filter** | 语义相似度过滤 | papers[], config, domains | filter_result | numpy, sentence-transformers |
| **recommender** | 推荐排序 | filter_result | recommendations | 无 |
| **reporter** | 日报 Markdown 生成 | all data | .md file | 无 |
| **aggregator** | 多日数据聚合 | date range | merged papers[] | crawler, filter, labeler |
| **analyzer** | LLM 中文摘要生成 | papers[] | analysis dict | claude CLI / OpenAI API |
| **weekly** | 周报生成（主入口） | date range | 周报.md | aggregator, recommender, trend, context_injector |
| **monthly** | 月报生成 | year+month | 月报.md | weekly, aggregator |
| **paper_db** | SQLite 知识图谱存储 | papers/edges | DB 读写 | sqlite3 |
| **semantic_scholar** | S2 API 客户端 | arxiv ID | 论文元数据+引用 | urllib |
| **init_graph** | BFS 领域扩散初始化 | seeds | paper_network.db | paper_db, semantic_scholar |
| **baseline_extractor** | 关键词 baseline 提取 | papers[] | baselines + edges | paper_db |
| **reference_ranker** | LLM 引用排序+方法变体 | paper + refs | ranked refs + variants | paper_db, LLM |
| **paper_analyst_v3** | H3 生产分析入口 | arxiv_id + title | 结构化分析 JSON + DB 状态 | paper_db, config_parser, OpenClaw |
| **context_injector** | 图谱上下文注入 | paper_id + DB | context block | paper_db |
| **trend** | 关键词趋势+Idea Seeds | papers[] | trend section | config_parser |
| **obsidian_writer** | Obsidian 笔记生成 | papers[] | .md per paper | paper_db |

---

## 三、数据库 Schema

**文件**: `data/paper_network.db` (SQLite)

```sql
-- 论文表
papers (
    id TEXT PRIMARY KEY,          -- arxiv ID (如 "2406.07550")
    title TEXT, abstract TEXT,
    authors TEXT,                  -- JSON array
    date TEXT,                     -- "YYYY-MM-DD"
    domain TEXT, best_score REAL,
    paper_type TEXT,               -- "方法文" | "Benchmark" | "Survey"
    labels TEXT,                   -- JSON array
    source TEXT,                   -- "seed" | "s2_expansion" | "daily"
    s2_id TEXT,                    -- Semantic Scholar paper ID
    s2_citation_count INTEGER,
    analysis_status TEXT,          -- pending / analyzing / completed / failed
    analysis_date TEXT,            -- 分析日期
    analysis_model TEXT,           -- 使用模型
    analysis_session_id TEXT,      -- OpenClaw session ID
    analysis_transcript TEXT,      -- transcript 路径
    analysis_result_path TEXT,     -- 结果 JSON 存档路径
    created_at TEXT
)

-- 论文关系边
paper_edges (
    src_id TEXT, dst_id TEXT,
    edge_type TEXT,                -- CITES | COMPARES_WITH | EXTENDS | SIMILAR_TO
    weight REAL DEFAULT 1.0,
    metadata TEXT,                 -- JSON
    PRIMARY KEY (src_id, dst_id, edge_type)
)

-- Baseline 记录
baselines (
    paper_id TEXT, name TEXT,
    canonical_name TEXT,           -- 标准化名称
    PRIMARY KEY (paper_id, canonical_name)
)

-- 方法名注册
methods (
    name TEXT PRIMARY KEY,
    aliases TEXT,                  -- JSON array
    category TEXT
)

-- 方法变体标签 (v2.1+)
method_variants (
    paper_id TEXT,
    base_method TEXT,              -- 如 "VQGAN"
    variant_tag TEXT,              -- 如 "vqgan:1d-tokenization"
    description TEXT,
    PRIMARY KEY (paper_id, variant_tag)
)
```

---

## 四、关键数据文件

| 路径 | 用途 | 生命周期 |
|------|------|----------|
| `config.template.md` | 默认配置模板 | 持久 |
| `data/paper_network.db` | 知识图谱数据库 | 持久，增量更新 |
| `data/cache/YYYY-MM-DD.json` | 每日爬取缓存（避免重复爬） | 持久，按日归档 |
| `data/cache/analysis_merged.json` | LLM 中文摘要缓存 | 持久，增量合入 |
| `data/cache/analysis_v3/*.json` | paper-analyst-v3 结构化分析存档 | 持久，按论文归档 |
| `data/cache/variant_batch*.json` | LLM 方法变体分析结果 | 持久，结果已入 DB |
| `data/logs/*.log` | 运行日志 | 保留 30 天 |
| `data/seeds/` | 领域种子论文列表 | 持久 |

---

## 五、配置项参考

配置文件为 Markdown 格式（`config.template.md`），关键字段：

```markdown
## 领域 (domains)
- name / keywords / seed_papers

## VIP 作者 (vip_authors)
- 名单 + fuzzy match

## 阈值 (thresholds)
- embedding_model: all-MiniLM-L6-v2
- threshold_mode: adaptive
- top_k: 30
- floor_threshold: 0.40

## 输出 (output)
- report_path: ~/mydata/notes/01-Diary/科研追踪/
- obsidian_vault: ~/mydata/notes/01-Diary/科研追踪/论文卡片/

## PKN (知识图谱)
- db_path: data/paper_network.db
- s2_interval: 8.0
- bfs_depth: 2
- bfs_max_papers: 500
```

---

## 六、开发规范

### 新增模块
1. 放在 `scripts/` 目录下
2. 必须有 `logger = logging.getLogger(__name__)`
3. 所有 DB 操作通过 `PaperDB` 类，不直接 `sqlite3.connect`
4. 底部加 `if __name__ == "__main__":` 自测代码
5. 写对应的测试报告到 `tests/reports/`

### Git 工作流
- `main`: 受保护，只接受 PR
- `dev`: 开发分支，feature 完成后 PR 到 dev
- `feature/*`: 每个功能一个分支
- commit message: `feat:` / `fix:` / `docs:` / `refactor:` 前缀

### 测试
- 每个模块有 `__main__` 自测
- 集成测试: `python3 -c "from paper_db import PaperDB; ..."` 方式
- 测试报告: `tests/reports/XX-name-test.md`

---

## 七、已知问题与审查记录

### 2026-03-11 架构审查

| ID | 级别 | 问题 | 状态 |
|----|------|------|------|
| P1 | 🔴 | 日报论文不进 DB，知识图谱不增长 | ✅ 已修: main.py 末尾调用 update_db_from_daily |
| P2 | 🔴 | 4 模块绕过 PaperDB 直接 sqlite3 | ✅ 已修: 全部迁入 PaperDB 方法 |
| P3 | 🟡 | baseline_extractor 与 reference_ranker 职责重叠 | ✅ 已合并: extractor 降级为 heuristic fallback |
| P4 | 🟡 | cache 文件 6/16 个冗余 | ✅ 已清理: 16→10 文件 |
| P5 | 🟡 | config_parser + trend 无日志 | ✅ 已修: 统一 log_config.py |
| P6 | 🟡 | 8 处硬编码路径 | ✅ 已修: 改为 SKILL_DIR/CACHE_DIR/DB_PATH 常量 |
| P7 | 🟢 | 无运行日志文件（跑完即失） | ✅ 已修: data/logs/YYYY-MM-DD.log |

---

## 八、S2 API 限速参数

基于 2026-03-11 首次运行的 429 分析：

| 参数 | 值 | 来源 |
|------|-----|------|
| 基础间隔 | 8.0s | 实测: @3.5s → 40% 429 rate |
| burst cooldown | 每 12 次请求暂停 15s | 实测: 平均 4.2 次调用触发 429 |
| 429 重试等待 | 45s | S2 需要较长冷却 |
| 每日增量上限 | 20 篇新论文的 S2 调用 | 控制 API 成本 |

---

## 九、paper-analyst Agent 架构（v3，2026-03-13）

### 概览

paper-analyst 是一个独立的 OpenClaw 命名 agent，负责单篇论文的深度结构化分析。
与 arxiv-radar 主流程解耦：Mox 通过 `sessions_spawn` 调用，只传入 arxiv_id + 任务描述 + S2 引用列表，接收返回的结果 json 路径。

```
Mox (arxiv-radar main pipeline)
  │
  ├─ sessions_spawn(task=template, model=minimaxm25)
  │
  ▼
paper-analyst Agent
  ├─ 检查 memory 缓存（已分析过？）
  ├─ arxiv-fetch skill → papers/{id}/ 源文件
  ├─ 读取 .tex / cite_map.json / paper_annotated.txt
  ├─ 按任务格式输出分析结果
  ├─ 写入 papers/{id}/analyse-results/results_YYYYMMDD.json（生产）或 results_YYYYMMDD_{scheme}.json（测试）
  └─ 返回该 json 完整路径（最后一行仅输出绝对路径，无其他文字）
  │
  ▼
Mox 收到路径
  ├─ 读取 json，提取 core_cite
  ├─ 与 S2 引用列表做高相似度匹配（≥0.8）
  ├─ 写回验证字段（_verified / _matched / _similarities）
  └─ 汇报幻觉率用于 Phase 1 G/H 测试
```

### Agent 配置

| 参数 | 值 |
|------|-----|
| Agent ID | `paper-analyst` |
| 模型 | `wq/minimaxm25`（默认），`wqoai/gpt52`（fallback） |
| Workspace | `~/.openclaw/workspace-paper-analyst/` |
| Discord | `#paper-analyse`（channel `1481818865385209926`） |
| 工具权限 | 全权限（read / exec / write / memory_search / memory_get） |

### 归档结构

```
~/.openclaw/workspace-paper-analyst/papers/
├── {arxiv_id}/
│   ├── *.tex / *.bbl / *.bib / cite_map.json  ← arxiv-fetch 输出（源文件）
│   ├── paper_annotated.txt                     ← PDF fallback
│   └── analyse-results/
│       ├── results_20260313.json               ← 生产分析
│       ├── results_20260314_H2.json            ← 测试分析（带 scheme 后缀）
│       └── ...                                 ← Mox verify 后验证字段写回此文件
└── ...
```

> **路径规范**：分析结果统一存放在 `analyse-results/` 子目录，与源文件隔离。
> Mox 验证后将 `_verified`/`_matched`/`_similarities` 等字段直接写回该 JSON 文件。

## 十、paper_analyst_v3.py（生产入口，2026-03-15）

### 职责

- 暴露 `analyse_paper(arxiv_id, title)` 作为生产入口
- 解析 `llm_analyse` 配置，统一选择模型、prompt 和超时
- 调用 `paper-analyst` agent；当前仓库仅保留 `sessions_spawn` 预留接口
- 复用 H3 测试验证过的容错 JSON 读取逻辑，兼容 GLM5 风格输出
- 将分析状态、session 溯源、结果路径统一写入 `PaperDB`
- 预留 S2 引用后验证钩子，当前先利用 DB 中的 `CITES` 出边做精确匹配

### 调用链

```text
main.py / weekly.py
  -> analyse_paper(arxiv_id, title)
  -> spawn_analyst()
  -> paper-analyst agent (OpenClaw runtime)
  -> safe_load_json()
  -> verify_analysis_result()
  -> PaperDB.update_analysis_status()
```

### 输出 JSON 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `arxiv_id` | string | arxiv 论文 ID |
| `title` | string | 论文标题 |
| `date` | string | 分析日期 |
| `task_summary` | string | 1-2句任务描述 |
| `model` | string | 使用的模型 |
| `abstract_en` | string | 英文原摘要 |
| `cn_oneliner` | string | 一句话总结（基于X引入Y实现Z） |
| `cn_abstract` | string | 中文摘要（2-4句） |
| `contribution_type` | enum | incremental / significant / story-heavy / foundational |
| `editorial_note` | string | 三段判断（前驱/贡献/评价），80-150字 |
| `why_read` | string | 适读人群及理由 |
| `method_variants` | list | base_method:variant_tag 对 |
| `core_cite` | list | ≥10条，含 title/arxiv_id/role/note |
| `idea` | list | 3条研究 idea，含 title + why |
| `core_cite_verified` | bool | 初始 false，Mox 验证后写回 true |
| `_verified_by` | string | "Mox"（Mox 写回） |
| `_verified_at` | string | 验证日期（Mox 写回） |
| `_matched` | int | 通过验证的 core_cite 数量（Mox 写回） |
| `_total` | int | core_cite 总数（Mox 写回） |
| `_similarities` | list | 每条 core_cite 的相似度得分（Mox 写回，debug 用） |

### Mox 调用任务模板

完整模板文件：`test_gh/task_template_v3.txt`

简要结构：
1. `用 arxiv-fetch skill 获取论文 {arxiv_id}`
2. 字段要求（含 2-3 个通用示例）
3. `确保每条 core_cite title 来自文末参考列表`
4. 附 S2 引用列表（来自 DB CITES 边）

### Memory 索引格式

写入 `memory/YYYY-MM-DD.md`（轻量索引，完整内容在 json 文件）：

```markdown
## {arxiv_id} | {论文标题}
- date: YYYY-MM-DD
- task_summary: {任务描述}
- result: {papers/{arxiv_id}/analyse-results/results_YYYYMMDD.json 完整路径}（测试期可带 _scheme 后缀）
```

### 双模式说明

| 模式 | 触发 | 输出 | 归档 |
|------|------|------|------|
| 结构化任务（Mox 调用） | task 含格式要求 | 仅返回 json 路径 | ✅ |
| 直接对话（用户聊天） | 无格式要求 | 自然语言（中文） | ✅ |

---

## §10 操作日志

所有对 arxiv-radar 项目的操作和改动记录在 `logs/YYYY-MM-DD.md` 中，保证项目可溯源。

```
arxiv-radar/logs/
├── 2026-03-14.md   ← 当日所有操作、改动、测试结果
├── 2026-03-15.md
└── ...
```

**规范：**
- 每次修改代码/配置/架构文档时，先在当日日志中记录改动内容
- 测试运行结果（通过/失败/重试）记录于日志
- Mox 每次操作 arxiv-radar 项目文件时，须同步追加日志条目
