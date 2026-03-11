# P3 首次领域扩散探索 — 测试报告

**测试日期**: 2026-03-11  
**分支**: `feature/p2-p5-pkn-core`

## 测试结果

| # | 测试 | 状态 | 备注 |
|---|------|------|------|
| 3.1 | _is_recent_enough 日期过滤 | ✅ | 2019→过滤, 2025→保留, 无日期→保留 |
| 3.2 | _s2_paper_to_local S2格式转换 | ✅ | 作者/ID/日期正确提取 |
| 3.3 | 无 arxiv ID 论文 → None | ✅ | 只收录有 arxiv ID 的论文 |
| 3.4 | DEFAULT_SEEDS 结构 | ✅ | 2 领域 × 5 seeds |
| 3.5 | Obsidian note 生成（含 frontmatter） | ✅ | 写入 .md 文件，含 YAML + wiki-links 模板 |
| 3.6 | write_paper_notes 批量写入 | ✅ | skip_existing 防重复 |
| 3.7 | BFS 结构逻辑（伪）测试 | ✅ | queue/visited/depth 逻辑正确 |

## Obsidian Note 格式

- YAML frontmatter: id, title, date, domain, score, paper_type, baselines, status, tags
- 中文摘要 + 一句话通俗解释
- 方法线索: 继承自 / 对比 Baseline / 同线路论文 / 引用（均为 [[wiki-links]]）
- 阅读笔记模板（核心贡献/方法概要/实验亮点/与研究关联/待读）

## 已知限制

- BFS 完整运行（depth=2, 800篇）约需 45-60 分钟（S2 API 3.5s/request × ~1000 calls）
- 建议首次运行前设置好 `--max-papers 300 --depth 1` 的快速版本
