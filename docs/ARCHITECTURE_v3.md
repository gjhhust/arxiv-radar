# arxiv-radar v3 Architecture

> 三队列并行架构设计文档
>
> **版本**: v3.1
> **日期**: 2026-03-15
> **状态**: 设计冻结，待实现

---

## 一、设计目标

v3 的目标不是给 v2 打补丁，而是把“抓取引用网络”和“LLM 深度分析”拆成可独立推进、可持久恢复、可审计追溯的流水线。

核心原则：

1. **实现一致性优先**：文档必须与当前 `scripts/paper_analyst_v3.py`、`scripts/paper_db.py`、`TEST_PLAN.md` 保持一致。
2. **DB 为单一事实源**：论文元数据、图谱边、分析状态统一收敛到 SQLite；大体积分析结果保留为 JSON 文件，DB 只保存路径与状态。
3. **队列可恢复**：OpenClaw / agent 重启后可从 DB 中恢复，不依赖内存态。
4. **写路径可追踪**：每次分析都要能追溯到模型、session、transcript、结果文件。
5. **错误隔离**：抓取失败、LLM 失败、结果校验失败分别处理，避免一个阶段拖垮整条流水线。

---

## 二、系统概览

```text
┌──────────────────────────────────────────────────────────────────────┐
│                          ARXIV-RADAR v3                             │
│                                                                      │
│  config.template.md / config.md                                      │
│          │                                                           │
│          ▼                                                           │
│  ┌────────────────┐   ┌────────────────┐   ┌──────────────────────┐ │
│  │ Fetch Queue    │   │ Analyse Queue  │   │ Cited-By Queue       │ │
│  │ 快速、IO 密集   │   │ 慢速、LLM 密集  │   │ 预留扩展              │ │
│  │                │   │                │   │                      │ │
│  │ S2 metadata    │   │ paper_analyst  │   │ 潜力论文挖掘          │ │
│  │ S2 refs/citers │   │ JSON verify    │   │ cited-by ranking     │ │
│  └──────┬─────────┘   └──────┬─────────┘   └──────────┬───────────┘ │
│         │                    │                        │             │
│         └────────────────────┼────────────────────────┘             │
│                              ▼                                      │
│      ┌──────────────────────────────────────────────────────────┐    │
│      │ SQLite: papers / paper_edges / baselines / methods      │    │
│      │ + queue tables (v3 新增)                                │    │
│      └──────────────────────────────────────────────────────────┘    │
│                              │                                      │
│                              ▼                                      │
│        data/cache/analysis_v3/{arxiv_id}_{timestamp}.json           │
└──────────────────────────────────────────────────────────────────────┘
```

当前实现锚点：

- `scripts/paper_analyst_v3.py` 已实现分析重试、fallback model、JSON 修复、S2 引用后验校验、结果归档。
- `scripts/paper_db.py` 已实现 `papers` / `paper_edges` / `baselines` / `methods` 四张核心表。
- `tests/active/test_paper_analyst_v3_smoke.py` 已覆盖一次失败后重试成功、引用匹配成功、DB 状态落盘成功。

因此，v3 的首要工程目标不是重新设计分析模块，而是围绕它补齐：

1. 队列持久化
2. Fetch 阶段
3. 调度与重试
4. 运行日志与运维规范

---

## 三、三队列职责与边界

### 3.1 Queue 1: Fetch Queue

**职责**

- 拉取论文基础元数据
- 拉取 Semantic Scholar 引用 / 被引关系
- 补全 `papers` 和 `paper_edges`
- 为 Analyse Queue 提供可分析输入

**输入来源**

1. 用户配置的 seed papers
2. 增量扫描到的新论文
3. 分析结果中的 `core_cite`
4. 未来 cited-by queue 的推荐结果

**输出**

- `papers` 记录存在且 metadata 基本完整
- `paper_edges` 中存在 `CITES` 边
- `analysis_queue` 可消费的 paper id

**约束**

- Fetch 只负责“图谱事实”，不负责 LLM 推理
- 任何引用标题匹配、故事线排序都不在此阶段做
- 写入必须幂等，允许重复入队

### 3.2 Queue 2: Analyse Queue

**职责**

- 调用 `paper_analyst_v3.analyse_paper()`
- 维护 `analysis_status` / `analysis_model` / `analysis_session_id`
- 将分析结果归档到 `data/cache/analysis_v3/`
- 基于 `paper_edges(CITES)` 做后验校验，补 `_verified` / `_matched` / `_similarities`
- 抽取 `core_cite` 中的标题，回灌到 Fetch Queue

**已实现事实**

- 默认模型：`wq/minimaxm25`
- fallback：`wq/glm5`
- `max_retries` 和 `timeout_seconds` 从 `llm_analyse` 配置读取
- 结果文件异常时会尝试 `fix_json_llm_output()`

**不做的事情**

- 不直接在 DB 里展开写 `core_cite` 明细列
- 不在分析阶段直接写 `EXTENDS` / `COMPARES_WITH` 等高阶边
- 不自动打最终业务标签

### 3.3 Queue 3: Cited-By Queue

**定位**

- 这是 v3 的扩展位，不是 Phase 1 的交付物
- 目标是对 seed / watchlist 论文的被引论文做二次筛选，发现值得补图谱和深度分析的新工作

**延后原因**

- 当前 `paper_db.py` 已有 `EDGE_CITED_BY` 常量，但主流程仍以 `CITES` 为主
- cited-by 价值判断需要额外排序策略，尚无稳定测试基线

---

## 四、数据模型

本节区分两层：

1. **现有实现**：已经在 `paper_db.py` 中落地
2. **v3 新增**：为三队列调度补齐的表

### 4.1 `papers` 表

`papers` 是单篇论文的主记录。当前实现字段如下。

```sql
CREATE TABLE papers (
    id TEXT PRIMARY KEY,                    -- arXiv ID, 允许带版本号
    s2_id TEXT,                            -- Semantic Scholar paper ID
    title TEXT NOT NULL,
    abstract TEXT,
    authors TEXT,                          -- JSON array[str]
    author_ids TEXT,                       -- JSON array[str]
    date TEXT,                             -- YYYY-MM-DD
    year INTEGER,
    arxiv_url TEXT,
    arxiv_categories TEXT,                 -- JSON array[str]
    primary_category TEXT,
    doi TEXT,
    venue TEXT,
    venue_short TEXT,
    domain TEXT,
    best_score REAL DEFAULT 0,
    paper_type TEXT DEFAULT '方法文',
    labels TEXT,                           -- JSON array[str]
    cn_abstract TEXT,
    cn_oneliner TEXT,
    tldr TEXT,
    s2_citation_count INTEGER DEFAULT 0,
    s2_reference_count INTEGER DEFAULT 0,
    s2_influential_citation_count INTEGER DEFAULT 0,
    s2_fields_of_study TEXT,               -- JSON array[str]
    s2_words TEXT,                         -- JSON array[str]
    is_open_access INTEGER DEFAULT 0,
    open_access_pdf TEXT,
    keywords TEXT,                         -- JSON array[str]
    tasks TEXT,                            -- JSON array[str]
    methods TEXT,                          -- JSON array[str]
    datasets TEXT,                         -- JSON array[str]
    method_variants TEXT,                  -- JSON array[object]
    baselines_json TEXT,                   -- JSON array[object]
    motivation_sources TEXT,               -- JSON array[object]
    institutions TEXT,                     -- JSON array[str]
    code_url TEXT,
    github_stars INTEGER DEFAULT 0,
    source TEXT DEFAULT 'arxiv',
    status TEXT DEFAULT 'pending',
    analysis_status TEXT DEFAULT 'pending',
    analysis_date TEXT,
    analysis_model TEXT,
    analysis_session_id TEXT,
    analysis_transcript TEXT,
    analysis_result_path TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
```

**字段分组说明**

| 分组 | 字段 | 说明 |
|------|------|------|
| 主键与外部标识 | `id`, `s2_id` | `id` 是主键；`s2_id` 用于与 S2 对齐 |
| 基础元数据 | `title`, `abstract`, `authors`, `date`, `venue`, `doi` | Fetch 阶段主要维护 |
| 分类与筛选 | `domain`, `best_score`, `paper_type`, `labels`, `source` | Daily / incremental / seed 流程使用 |
| S2 扩展信息 | `s2_*`, `tldr`, `open_access_pdf` | 用于排序、报告和后续图谱推断 |
| 结构化分析结果 | `cn_abstract`, `cn_oneliner`, `method_variants` 等 | 可由后处理脚本持续补写 |
| 分析运行状态 | `analysis_status`, `analysis_date`, `analysis_model` | `paper_analyst_v3.py` 当前已使用 |
| 审计追踪 | `analysis_session_id`, `analysis_transcript`, `analysis_result_path` | 用于复盘与重跑 |

**设计决策**

- `core_cite` 不单独落到 `papers` 列中，当前以结果 JSON 为准。
- 分析结果的大 JSON 不内联存入 DB，避免 schema 频繁变动和 SQLite 行膨胀。
- `status` 与 `analysis_status` 并存：前者保留旧流程兼容性，后者是 v3 主状态机。

### 4.2 `paper_edges` 表

```sql
CREATE TABLE paper_edges (
    src_id TEXT NOT NULL,
    dst_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,               -- CITES / CITED_BY / ...
    weight REAL DEFAULT 1.0,
    metadata TEXT,                         -- JSON
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (src_id, dst_id, edge_type)
);
```

**当前主要使用的边类型**

| 边类型 | 含义 | 当前是否生产使用 |
|--------|------|------------------|
| `CITES` | A 引用 B | 是 |
| `CITED_BY` | A 被 B 引用 | 预留 |
| `COMPARES_WITH` | 方法对比关系 | 预留 |
| `EXTENDS` | 明确扩展关系 | 预留 |
| `SIMILAR_TO` | 语义近邻 | 预留 |

**为什么 v3 仍以 `CITES` 为核心**

- `paper_analyst_v3.py` 的 `get_s2_reference_titles()` 只依赖 `EDGE_CITES`
- `tests/active/test_paper_analyst_v3_smoke.py` 也是通过 `db.add_edge(..., "CITES")` 构造验证集
- 这意味着队列第一阶段只需要把 `CITES` 图补齐，就能支撑分析校验和后续推荐

### 4.3 `baselines` / `methods` 表

这两张表在 v3 初期不是核心，但必须保留，因为它们已经在 v2 schema 中存在，未来方法图谱会用到。

```sql
CREATE TABLE baselines (
    paper_id TEXT NOT NULL,
    baseline_name TEXT NOT NULL,
    canonical_name TEXT,
    context TEXT,
    PRIMARY KEY (paper_id, baseline_name)
);

CREATE TABLE methods (
    canonical_name TEXT PRIMARY KEY,
    aliases TEXT,
    description TEXT,
    first_paper_id TEXT,
    category TEXT
);
```

### 4.4 v3 新增：队列表

初稿中的 `queue_state(pending_papers JSON)` 过于粗糙，不利于：

- 单条任务重试
- 死信隔离
- 统计不同来源
- 多 worker 并发抢占

v3 建议改成“任务表 + 运行表”。

#### `queue_jobs`

```sql
CREATE TABLE queue_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_type TEXT NOT NULL,              -- fetch / analyse / cited_by
    paper_id TEXT NOT NULL,
    priority INTEGER DEFAULT 100,          -- 越小优先级越高
    not_before TEXT,                       -- 延迟重试时间
    status TEXT NOT NULL DEFAULT 'pending',-- pending / leased / done / failed / dead
    source TEXT NOT NULL,                  -- seed / incremental / core_cite / manual
    payload TEXT,                          -- JSON 扩展上下文
    dedupe_key TEXT NOT NULL,              -- 例如 "fetch:2501.00001"
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    last_error TEXT,
    leased_by TEXT,
    leased_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(dedupe_key)
);
```

#### `queue_runs`

```sql
CREATE TABLE queue_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    queue_type TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    started_at TEXT DEFAULT (datetime('now')),
    finished_at TEXT,
    outcome TEXT,                          -- success / retry / failed / dead
    error_type TEXT,
    error_message TEXT,
    metrics TEXT                           -- JSON: latency_ms / api_calls / token_usage
);
```

**为什么这样设计**

- `queue_jobs` 表达“当前任务状态”
- `queue_runs` 表达“历史执行轨迹”
- 崩溃恢复时只需要把超时 `leased` 任务回滚到 `pending`
- 日志与统计不必从文本日志倒推

### 4.5 建议索引

```sql
CREATE INDEX idx_queue_jobs_pick
ON queue_jobs(queue_type, status, priority, not_before, created_at);

CREATE INDEX idx_queue_jobs_paper
ON queue_jobs(paper_id, queue_type);

CREATE INDEX idx_queue_runs_job
ON queue_runs(job_id, started_at);
```

---

## 五、队列处理流程

### 5.1 总体状态图

```text
seed/config/incremental/core_cite
            │
            ▼
     queue_jobs(status=pending)
            │
      scheduler lease
            ▼
     queue_jobs(status=leased)
            │
   ┌────────┼────────┐
   │                 │
success            failure
   │                 │
   ▼                 ▼
 done          retry_count + 1
                     │
             retry_count <= max_retries ?
                     │
            ┌────────┴────────┐
            │                 │
           yes               no
            │                 │
            ▼                 ▼
     status=pending      status=dead
     not_before=...      last_error=...
```

### 5.2 Fetch Queue 详细流程

```text
1. scheduler 选择 queue_type=fetch 的最早可执行任务
2. worker lease job
3. 若 papers 中已存在且 metadata 足够新:
   - 直接标记 done，必要时仍补边
4. 调用 Semantic Scholar:
   - 获取论文 metadata
   - 获取 reference 列表
   - 获取 citation 列表（若成本可接受）
5. upsert_paper()
6. 为每个 reference 写 CITES 边: current -> ref
7. 为每个 citation 写 CITES 边: citer -> current
8. 对未知 paper_id:
   - 入 fetch queue
   - 若满足分析条件，也入 analyse queue
9. 写 queue_runs 统计
10. 标记当前 job 为 done
```

### 5.3 Analyse Queue 详细流程

```text
1. scheduler 选择 queue_type=analyse 的最早可执行任务
2. 检查 papers 记录是否存在，不存在则先补 fetch job
3. 检查 `analysis_status`:
   - completed + result_path 存在: 直接 done
   - analyzing 且 lease 未过期: 跳过
4. 调用 analyse_paper()
5. analyse_paper() 内部:
   - update_analysis_status(..., "analyzing")
   - spawn paper-analyst
   - safe_load_json() + fix_json_llm_output()
   - get_s2_reference_titles()
   - verify_analysis_result()
   - _persist_result_copy()
   - update_analysis_status(..., "completed")
6. 读取结果中的 core_cite.title:
   - 做标题标准化
   - 若 DB/S2 已能解析到 paper_id，则直接入 fetch/analyse
   - 若暂时只能拿到 title，则写 payload 等待解析器处理
7. 写 queue_runs
8. 标记 job done
```

### 5.4 伪代码

```python
def process_queue_job(db, queue_type, worker_id, now):
    job = lease_next_job(db, queue_type=queue_type, worker_id=worker_id, now=now)
    if not job:
        return None

    run_id = start_queue_run(db, job, worker_id)
    try:
        if queue_type == "fetch":
            process_fetch_job(db, job)
        elif queue_type == "analyse":
            process_analyse_job(db, job)
        elif queue_type == "cited_by":
            process_cited_by_job(db, job)
        else:
            raise ValueError(f"unknown queue_type={queue_type}")

        mark_job_done(db, job["id"])
        finish_queue_run(db, run_id, outcome="success")
    except RetryableError as exc:
        schedule_retry(
            db,
            job_id=job["id"],
            error=str(exc),
            not_before=backoff_time(job["retry_count"]),
        )
        finish_queue_run(db, run_id, outcome="retry", error_type=type(exc).__name__)
    except Exception as exc:
        move_to_dead_or_fail(db, job, exc)
        finish_queue_run(db, run_id, outcome="failed", error_type=type(exc).__name__)
        raise
```

---

## 六、错误处理与重试机制

### 6.1 错误分类

| 类别 | 示例 | 是否重试 | 处理方式 |
|------|------|----------|----------|
| 瞬时外部错误 | S2 429、网络抖动、spawn 超时 | 是 | 指数退避 + `not_before` |
| 结果格式错误 | JSON 未转义双引号 | 先修复再继续 | `fix_json_llm_output()` |
| 模型级失败 | minimax 失败、fallback 可用 | 是 | 切换到 fallback model |
| 数据缺失 | DB 无引用边、paper stub 缺字段 | 视场景 | 回补 fetch job |
| 不可恢复错误 | prompt 模板缺失、结果文件不存在 | 否 | 标记 failed/dead，人工介入 |

### 6.2 Fetch 重试策略

建议值：

- `max_retries = 4`
- backoff: `1m -> 5m -> 15m -> 1h`
- S2 429 单独最少等待 `45s`

失败判定：

- 达到 `max_retries` 后转 `dead`
- `last_error` 记录最后一次异常摘要
- `queue_runs` 中保留完整执行轨迹

### 6.3 Analyse 重试策略

当前实现已经有一层重试：

- `llm_analyse.max_retries`
- 模型 fallback：`default_model -> fallback_model`

队列层再包一层外部重试：

1. **函数内重试**：处理模型或单次 spawn 失败
2. **任务级重试**：处理运行时环境异常，如 OpenClaw 暂时不可用

建议约束：

- 同一篇论文单日最多外部重试 2 次
- 如果结果文件连续两次解析失败，直接进入 `dead`，避免无限循环覆盖坏结果

### 6.4 死信处理

`status = dead` 的任务不自动重试，只允许：

1. 人工修复配置 / prompt / 数据
2. 手动清空 `last_error`
3. 重置为 `pending`

这比“失败后永远自动重跑”安全，因为错误日志明确要求避免无确认的长时间错误计算。

---

## 七、日志规范

日志分三层：

### 7.1 应用日志

路径：`data/logs/YYYY-MM-DD.log`

格式建议：

```text
2026-03-15 10:20:31 INFO scripts.fetch_queue: leased fetch job paper=2501.00001 source=seed worker=fetch-1
2026-03-15 10:20:42 WARNING scripts.paper_analyst_v3: Repaired malformed JSON output: .../2501.00001.json
2026-03-15 10:21:03 ERROR scripts.fetch_queue: fetch failed paper=2501.00001 retry=2 error=HTTP 429
```

必备字段：

- timestamp
- level
- logger name
- queue_type
- paper_id
- worker_id / session_id
- outcome / error_type

### 7.2 任务审计日志

来源：`queue_runs.metrics` + `queue_runs.error_*`

用途：

- 看单任务耗时
- 看失败分布
- 支撑每周复盘

### 7.3 人工操作日志

路径：`logs/YYYY-MM-DD.md`

记录范围：

- 人工 rerun
- 手动修复 dead jobs
- prompt 切换
- schema 迁移

这与 `TEST_PLAN.md` 中的操作日志规范一致。

### 7.4 error-log 对齐要求

一旦出现以下情况，除正常日志外，还应追加到 `/Users/lanlanlan/.openclaw/workspace/memory/error-log.md`：

- 工具调用失败或返回异常
- 发现 prompt / 路径 / 子 agent 行为有新 gotcha
- 花费明显超预期
- 用户纠正新的流程硬规则

---

## 八、SQL 示例

### 8.1 取待执行 analyse job

```sql
SELECT id, paper_id, priority, retry_count, payload
FROM queue_jobs
WHERE queue_type = 'analyse'
  AND status = 'pending'
  AND (not_before IS NULL OR not_before <= datetime('now'))
ORDER BY priority ASC, created_at ASC
LIMIT 1;
```

### 8.2 查询某篇论文的引用标题，用于后验校验

这与 `paper_analyst_v3.py` 当前逻辑一致，只是 SQL 版展开：

```sql
SELECT p.id, p.title
FROM paper_edges e
JOIN papers p ON p.id = e.dst_id
WHERE e.src_id = ?
  AND e.edge_type = 'CITES'
ORDER BY p.title;
```

### 8.3 查询待重试的 dead / failed 分布

```sql
SELECT queue_type, status, COUNT(*) AS cnt
FROM queue_jobs
GROUP BY queue_type, status
ORDER BY queue_type, status;
```

### 8.4 查询最近 7 天完成分析的论文

```sql
SELECT id, title, analysis_date, analysis_model, analysis_result_path
FROM papers
WHERE analysis_status = 'completed'
  AND analysis_date >= datetime('now', '-7 day')
ORDER BY analysis_date DESC;
```

---

## 九、配置设计

### 9.1 最小配置示例

下面示例兼容当前实现的 `llm_analyse` 读取方式，并补入 v3 队列配置。

```yaml
domains:
  - name: "1D Image Tokenizer"
    keywords:
      - "image tokenizer"
      - "1D token"
      - "VQVAE"
    seed_papers:
      - "2406.07550"
      - "2503.08685"

date_range:
  earliest: "2024-01-01"
  incremental_days: 7

storage:
  db_path: "data/paper_network.db"
  analysis_result_dir: "data/cache/analysis_v3"
  log_dir: "data/logs"

fetch:
  enabled: true
  s2_interval_seconds: 8.0
  burst_size: 12
  burst_cooldown_seconds: 15
  max_retries: 4
  citation_fetch_enabled: true

llm_analyse:
  enabled: true
  default_model: "wq/minimaxm25"
  fallback_model: "wq/glm5"
  max_retries: 1
  timeout_seconds: 300
  prompt_template: "prompts/prompt_H_template.txt"

queues:
  scheduler_tick_seconds: 5
  lease_timeout_seconds: 1800
  analyse_concurrency: 1
  fetch_concurrency: 1
  cited_by_concurrency: 0
  default_priority:
    seed: 10
    incremental: 30
    core_cite: 50
    manual: 5

report:
  daily_format: "markdown"
  weekly_format: "markdown"
  monthly_format: "markdown+html"
```

### 9.2 调度策略结论

待讨论问题在本版收敛为以下决策：

1. **主排序采用“优先级 + 时间”**，不是纯时间优先。
2. 推荐优先级：
   - `manual` / 手工补跑
   - `seed`
   - `incremental`
   - `core_cite`
3. 同优先级内按 `created_at ASC`。

原因：

- 纯时间优先会让大批 core_cite 递归扩散淹没 seed 和增量入口
- seed 与手工修复通常更接近用户当前关注点

---

## 十、增量触发机制

### 10.1 触发源

1. **初始化触发**：首次加载 seed papers
2. **定时增量**：每日扫描最近 `N` 天论文
3. **分析回灌**：从 `core_cite` 派生新任务
4. **人工触发**：指定 arXiv ID 补抓 / 补分析

### 10.2 推荐落地方式

Phase 1 先支持：

- 手动 CLI 触发
- cron/heartbeat 触发单个入口脚本

不建议在 Phase 1 直接做“常驻 daemon”，原因：

- 当前 OpenClaw 子 agent 仍有路径、prompt、环境依赖约束
- `error-log.md` 明确说明长任务必须先计划并确认

因此推荐模型：

1. 一个幂等入口脚本 `run_v3_once.py`
2. 每次只跑有限批次
3. 调度器根据 DB 剩余任务继续下一轮

---

## 十一、报告格式决策

### 11.1 结论

- **日/周/月报主格式：Markdown**
- **对比测试和可视化复盘：HTML**

### 11.2 原因

- 现有仓库报告主链路、Obsidian 集成都偏向 Markdown
- `TEST_PLAN.md` 已经存在 HTML 对比报告（如 `report_h1_h2.html`）
- 架构文档不应引入新的报告主格式分叉

### 11.3 输出建议

1. 日报：Markdown，便于写入 Obsidian
2. 周报：Markdown 主体，可附 HTML 图表
3. 月报：Markdown + HTML 双产物
4. 每篇论文卡片：仍落 Markdown / JSON 双格式

---

## 十二、实施计划

### Phase 0: 对齐现状

目标：先把 v3 设计锚定到已存在代码。

任务清单：

1. 确认 `paper_analyst_v3.py` 为唯一分析入口
2. 确认 `analysis_result_path` 是结果真源
3. 清理旧文档中与实现不一致的字段描述
4. 固化 `prompt_template`、模型 fallback、JSON 修复约束

交付：

- 本文档
- 更新后的测试基线说明

### Phase 1: 队列 schema 与调度骨架

任务清单：

1. 在 `paper_db.py` 中新增 `queue_jobs` / `queue_runs`
2. 提供 schema migration
3. 新建 `scripts/queue_repo.py` 或在 `PaperDB` 中扩展队列 CRUD
4. 实现 lease / ack / retry / dead-letter 逻辑
5. 增加恢复脚本：回收超时 `leased` 任务

测试：

- 单元测试：幂等入队、去重、lease、重试、dead
- 集成测试：重启后任务恢复

建议 commit：

- `feat(db): add persistent queue tables for v3 pipeline`
- `feat(queue): implement lease retry and dead-letter flow`

### Phase 2: Fetch Queue 实现

任务清单：

1. 新建 `scripts/fetch_queue.py`
2. 接 Semantic Scholar 客户端
3. upsert `papers`
4. 写 `CITES` 边
5. 衍生 analyse jobs
6. 处理 429、空结果、重复任务

测试：

- mock S2 响应
- 429 重试测试
- 边去重测试
- seed 扩散 smoke test

建议 commit：

- `feat(fetch): add semantic scholar fetch worker`
- `test(fetch): cover retry dedupe and edge writes`

### Phase 3: Analyse Queue 编排

任务清单：

1. 新建 `scripts/analyse_queue.py`
2. 调用 `analyse_paper()`
3. 解析 `core_cite` 回灌 fetch jobs
4. 处理结果 JSON 丢失 / 损坏 / 校验为空
5. 记录 session / transcript / result_path

测试：

- 复用 `test_paper_analyst_v3_smoke.py`
- 增加队列级失败恢复测试
- 增加 fallback model 覆盖测试

建议 commit：

- `feat(analyse): wire paper_analyst_v3 into queue worker`
- `test(analyse): add queue-level retry coverage`

### Phase 4: 增量扫描与入口编排

任务清单：

1. 新建 `run_v3_once.py`
2. 增量扫描最近 N 天论文
3. 写入初始 fetch jobs
4. 跑一轮 scheduler
5. 输出本轮统计报告

测试：

- CLI 参数测试
- 限流和批次上限测试
- 端到端 smoke test

建议 commit：

- `feat(cli): add run_v3_once entrypoint`
- `test(e2e): add v3 seed-to-analysis smoke run`

### Phase 5: 报告与 cited-by 扩展

任务清单：

1. 把分析结果汇入日报 / 周报 / 月报
2. cited-by queue 原型
3. 高阶边推断（`EXTENDS` / `COMPARES_WITH`）
4. 可视化面板 / HTML 复盘页

---

## 十三、测试策略

### 13.1 测试分层

1. **Schema / DB 单测**
   - migration 可重复执行
   - 队列表去重和 lease 逻辑正确

2. **模块 smoke test**
   - `paper_analyst_v3.py` 已有 smoke test 继续保留
   - 新增 fetch/analyse worker 独立 smoke test

3. **集成测试**
   - seed -> fetch -> analyse -> result_path 全链路
   - 重启恢复
   - 失败重试后成功

4. **比较实验**
   - 延续 `TEST_PLAN.md` 的 H1/H2 prompt 对比
   - 只在 canonical template 确认后进入生产

### 13.2 当前必须守住的回归项

根据现有实现，至少保证：

1. 首次 spawn 失败后能重试成功
2. malformed JSON 能自动修复
3. `analysis_status` 能从 `analyzing -> completed`
4. `analysis_result_path` 文件真实存在
5. `_matched` 能匹配到 DB 中的 `CITES` 标题

### 13.3 建议命名

- 单测：`tests/unit/test_queue_repo.py`
- 模块 smoke：`tests/active/test_fetch_queue_smoke.py`
- 集成：`tests/active/test_v3_pipeline_smoke.py`

---

## 十四、Git 提交规范

沿用仓库现有约定，补充到 v3：

### 14.1 commit 前缀

- `feat:` 新功能
- `fix:` 缺陷修复
- `docs:` 文档更新
- `refactor:` 重构
- `test:` 测试
- `chore:` 非功能性维护

### 14.2 推荐粒度

每个 commit 只做一件事：

1. schema 迁移
2. fetch worker
3. analyse worker
4. CLI 编排
5. 测试
6. 文档

避免把“schema + worker + prompt + tests”揉成一个 commit。

### 14.3 推荐格式

```text
feat(queue): add persistent job leasing and retry state
fix(analyse): preserve result path after fallback retry
docs(architecture): align v3 queue design with paper_analyst_v3
test(v3): add seed-to-analysis smoke coverage
```

---

## 十五、关键设计决策

| 决策 | 结论 | 原因 |
|------|------|------|
| 分析结果存储 | JSON 文件为真源，DB 存路径 | 当前实现已稳定，schema 更轻 |
| 分析状态存储 | `papers.analysis_*` | 已被 `paper_analyst_v3.py` 使用 |
| 队列持久化方案 | 任务表，不用单行 JSON | 支持重试、死信、并发 |
| 引用图主边 | `CITES` | 当前验证逻辑已依赖 |
| 调度策略 | `priority + created_at` | 避免 core_cite 扩散淹没主任务 |
| 增量执行方式 | 幂等批处理，不做常驻 daemon | 更符合现阶段 OpenClaw 约束 |
| 报告主格式 | Markdown | 与现有报告链路一致 |

---

## 十六、与现有实现的一致性检查

本版已对齐以下事实：

1. `paper_analyst_v3.py` 当前只更新 `analysis_status` 相关字段，不直接把完整分析 JSON 写进 `papers`。
2. `verify_analysis_result()` 当前是“标题标准化后的精确匹配”，不是模糊相似度匹配。
3. `test_paper_analyst_v3_smoke.py` 当前验证的是 `_verified=True`、`_matched=["Known Ref"]` 和结果文件真实存在。
4. `paper_db.py` 当前核心边是 `CITES`；`CITED_BY` 等仍属扩展位。
5. `error-log.md` 中关于长任务、路径、prompt 完整传递的规则，要求 v3 实现优先采用小批量、可审计、可人工确认的执行模型。

---

## 十七、开放问题与当前答案

### 17.1 队列调度策略

**答案**：采用 `priority + FIFO`，不是纯时间优先。

### 17.2 错误处理

**答案**：两层重试，超过阈值进入 dead-letter，禁止无限自动重跑。

### 17.3 增量触发机制

**答案**：先做幂等批处理入口，由 cron/heartbeat 调；暂不做常驻服务。

### 17.4 报告格式

**答案**：Markdown 为主，HTML 用于实验对比和图表复盘。

---

**文档结论**：v3 已不再缺“概念草图”，当前真正的工程缺口是队列表、Fetch Worker 和调度入口。分析模块本身已具备生产原型能力，应在其外围补齐可恢复编排，而不是重写分析核心。
