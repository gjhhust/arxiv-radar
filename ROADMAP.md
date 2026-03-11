# arxiv-radar Roadmap

## v1.0 — 当前稳定版（已完成）

- [x] arXiv 日报爬取 + 语义过滤
- [x] VIP 作者检测 + 研究组标签
- [x] 论文类型分类（方法文/Benchmark/Survey）
- [x] 中文摘要生成（Claude sub-agent）
- [x] 周报 / 月报聚合
- [x] Obsidian vault 输出

## v2.0 — Paper Knowledge Network（PKN）

> **目标**：从「每日推送」升级为「领域知识网络」，辅助 idea 涌现和科研方向把握。

### Phase 1: 存储层 + Semantic Scholar 接入
- [x] SQLite schema（papers + paper_edges + baselines + methods）
- [x] Semantic Scholar API 模块（引用/被引用/参考文献）
- [ ] 引用边 CITES / CITED_BY 构建
- [ ] 单元测试 + 测试报告

### Phase 2: Baseline 提取 + 方法线构建
- [x] sub-agent baseline 抽取器（批量处理摘要）
- [ ] COMPARES_WITH 边 + EXTENDS 边
- [x] 方法名标准化（canonical method names）
- [ ] 单元测试 + 测试报告

### Phase 3: 首次领域扩散探索
- [ ] 从种子论文 BFS 展开（深度 2，时间过滤 2 年内）
- [ ] 初始图构建脚本 `scripts/init_graph.py`
- [ ] 生成 Obsidian 笔记网络（含 [[wiki-links]]）
- [ ] 集成测试 + 测试报告

### Phase 4: Context 注入 + 增量更新
- [ ] 每日分析时注入上下文（前驱论文 + 同 baseline 论文）
- [ ] 增量更新 pipeline 接入 weekly.py / reporter.py
- [ ] FAISS 索引增量更新
- [x] 端到端集成测试

### Phase 5: Idea 涌现引擎
- [ ] 每周 idea seeds section（子 agent 从图中涌现 idea）
- [ ] 趋势雷达（方法频率统计 + 上升趋势检测）
- [x] 个人研究对齐评分

### Release: v2.0 稳定版
- [ ] 整体联调测试
- [ ] 文档更新（README + SKILL.md + TUNING_NOTES.md）
- [ ] 发布 v2.0 tag
