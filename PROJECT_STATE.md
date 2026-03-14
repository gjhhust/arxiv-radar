# PROJECT_STATE.md — arxiv-radar v3 协作状态文件

> 本文件是 Mox（主 agent）和 Codex（持久编码 session）的共享状态锚点。
> 每次 Codex 完成一个里程碑，必须更新本文件。
> Mox 每次被 cron 唤醒，读本文件重建上下文。

---

## 基本信息

- **项目**: arxiv-radar v3
- **仓库路径**: `/Users/lanlanlan/.openclaw/workspace/skills/arxiv-radar`
- **分支**: `feature/paper-analyst-v3`（実際の作業ブランチ。PROJECT_STATE.md の旧記述は誤り）
- **架构文档**: `docs/ARCHITECTURE_v3.md`（v3.1，设计冻结）
- **DB 路径**: `data/paper_network.db`
- **启动时间**: 2026-03-15 01:56 CST

---

## 当前阶段

**Phase 6: Daily Report Integration**

状态: `🟡 待开始`

### Phase 6 任务清单

- [x] `scripts/post_process.py`：从分析结果 JSON 提取 core_cite → 写入 DB 边（CITES/EXTENDS） — commit `8a4660e`，21个单元测试
- [x] `scripts/reporter_v3_enrich.py`：v3 分析字段注入报告层（新文件，无需修改现有文件）— commit `58bc359`，32个单元测试
- [ ] 更新 `scripts/main.py` + `scripts/reporter.py` 调用 v3 pipeline ⚠️ **需修改现有文件** — 变更方案见 `docs/PHASE6_INTEGRATION_PROPOSAL.md`，等 guojiahao 确认
- [ ] 验证：运行后日报 / 周报包含 v3 分析结果（paper_type、score、core_cite 展示）
- [ ] Git commit: `feat(report): integrate v3 pipeline into daily/weekly report`

> 依赖：Phase 5 完成（✅）

---

## 已完成阶段

| Phase | 状态 | 完成时间 | 关键 commit |
|-------|------|----------|------------|
| Phase 0: 对齐现状 | ✅ 完成 | 2026-03-15 | `paper_analyst_v3.py` 已存在 |
| 架构文档 v3.1 | ✅ 完成 | 2026-03-15 | `docs/ARCHITECTURE_v3.md` |
| Phase 1: 队列 Schema | ✅ 完成 | 2026-03-15 02:30 | `d1e5dcb` |
| Phase 2: Fetch Queue | ✅ 完成 | 2026-03-15 03:05 | `d208b28` |
| Phase 3: Analyse Queue | ✅ 完成 | 2026-03-15 03:50 | `383c364` |
| Phase 4: Pipeline Entry | ✅ 完成 | 2026-03-15 04:00 | `39a3d25` |
| Phase 5: E2E Smoke Test | ✅ 完成 | 2026-03-15 04:48 | `7ed9562` |

---

## 架构关键约束（Codex 必须遵守）

1. **分析入口唯一**: `scripts/paper_analyst_v3.py` 是唯一 LLM 分析入口，不要重写
2. **DB 唯一实例**: 所有 DB 操作通过 `scripts/paper_db.py` 的 `PaperDB` 类
3. **LLM 模型**: 默认 `wq/minimaxm25`，fallback `wq/glm5`；禁用 `gpt52`/`gemini31pro`
4. **JSON 容错**: 使用 `fix_json_llm_output()` + `safe_load_json()`（已在 run_h3_test.py 中）
5. **S2 API 限速**: 间隔 8.0s，每 12 次请求冷却 15s
6. **无第三方依赖**: 只用 Python stdlib（除了已有的依赖）
7. **分析结果存储**: JSON 文件为真源 (`data/cache/analysis_v3/`)，DB 存路径
8. **边类型**: 主要用 `CITES`，其他边类型预留接口即可
9. **队列调度**: `priority + FIFO`（manual=5 > seed=10 > incremental=30 > core_cite=50）
10. **错误隔离**: fetch 失败、LLM 失败、校验失败分别处理，不互相污染

---

## 队列表设计（Phase 1 目标）

### `queue_jobs` 表
```sql
CREATE TABLE queue_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_type TEXT NOT NULL,              -- fetch / analyse / cited_by
    paper_id TEXT NOT NULL,
    priority INTEGER DEFAULT 100,
    not_before TEXT,
    status TEXT NOT NULL DEFAULT 'pending',-- pending / leased / done / failed / dead
    source TEXT NOT NULL,                  -- seed / incremental / core_cite / manual
    payload TEXT,
    dedupe_key TEXT NOT NULL,              -- "fetch:2501.00001"
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

### `queue_runs` 表
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

### 建议索引
```sql
CREATE INDEX idx_queue_jobs_pick ON queue_jobs(queue_type, status, priority, not_before, created_at);
CREATE INDEX idx_queue_jobs_paper ON queue_jobs(paper_id, queue_type);
CREATE INDEX idx_queue_runs_job ON queue_runs(job_id, started_at);
```

---

## Codex 工作日志

> Codex 每次完成里程碑后在此追加记录（时间 + 做了什么 + commit hash）

- **Phase 1**: 由 Mox 独立完成（模块化代码，Codex token 留给更大任务）

---

## Mox 协调日志

> Mox 每次 cron 唤醒后在此追加记录

- **2026-03-15 01:56**: 初始化项目状态，spawn Codex session，Phase 1 开始
- **2026-03-15 02:30**: [cron 触发] Phase 1 完成 —— 实现 queue_jobs/queue_runs 表、6个队列CRUD方法、23个单元测试全绿。commit: d1e5dcb。进入 Phase 2（Fetch Queue Worker）。
- **2026-03-15 03:05**: [cron 触发] Phase 2 完成 —— fetch_queue.py (process_fetch_job + run_fetch_batch)，14个单元测试全绿。CITES边写入、ref自动入队、seed/core_cite自动入analyse队列。commit: d208b28。进入 Phase 3（Analyse Queue Worker）。
- **2026-03-15 03:50**: [cron 触发] Phase 3 完成 —— analyse_queue.py (process_analyse_job + run_analyse_batch)，19个单元测试全绿（56 total）。core_cite回灌fetch队列、AnalyseError/AnalyseFatal分级。commit: 383c364。
- **2026-03-15 04:00**: [同 cron] Phase 4 完成 —— pipeline.py (seed_papers + run_pipeline + CLI)，9个单元测试全绿（65 total）。commit: 39a3d25。进入 Phase 5（E2E Smoke Test）。
- **2026-03-15 04:48**: [cron 触发] Phase 5 完成 —— tests/integration/test_e2e_pipeline.py 新增9个测试：TestDryRunCLI(1)、TestAnalysePhase(3)、TestFetchPhase(4)、TestFullPipeline(1)。非网络测试全绿，S2 网络测试正确 skip（sandbox 无法访问 S2）。branch 名更正为 feature/paper-analyst-v3。commit: 7ed9562。进入 Phase 6（Daily Report Integration）。
- **2026-03-15 05:48**: [cron 触发] Phase 6 进行中 —— post_process.py 新增（分析結果→DB同步：cn_oneliner/cn_abstract/paper_type/keywords/EXTENDS edge/method_variants/analysis_status），21个单元测试全绿（86 total）。commit: 8a4660e。阻塞：main.py 统合需改现有文件，等 guojiahao 确认。
- **2026-03-15 06:44**: [cron 触发] Phase 6 继续 —— reporter_v3_enrich.py 新增（v3字段注入报告层：enrich_paper_with_v3/render_v3_section/append_v3_section_to_report/core_cite格式化），32个单元测试全绿（118 total）。docs/PHASE6_INTEGRATION_PROPOSAL.md 写入精确变更方案（main.py 10行 + reporter.py 10行）。commit: 58bc359。等 guojiahao 确认后可立即执行。
