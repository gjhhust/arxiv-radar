# P5 Idea 涌现引擎 + 趋势雷达 — 测试报告

**测试日期**: 2026-03-11  
**分支**: `feature/p2-p5-pkn-core`

## 测试结果

| # | 测试 | 状态 | 备注 |
|---|------|------|------|
| 5.1 | extract_keywords_from_papers: 关键词统计 | ✅ | 26 tracked keywords 正确计数 |
| 5.2 | compute_trends: top/rising/new 分类 | ✅ | generation ↑80%, tokenizer ↑30% |
| 5.3 | format_trend_section: Markdown 渲染 | ✅ | 热门/上升/新出现三栏，带 bar chart |
| 5.4 | generate_idea_seeds: 4 种 idea 模式 | ✅ | 跨领域迁移 / 效率质量 / Benchmark驱动 / 趋势捕捉 |
| 5.5 | weekly.py 接入趋势+idea（try/except 降级） | ✅ | 失败时优雅跳过不影响主报告 |

## Trend 示例输出（5 篇测试论文）

热门关键词 top-3: `generation`, `autoregressive`, `unified`  
上升趋势: `generation` ↑80%, `multimodal` ↑40%  
新出现: `flow matching`, `reasoning`

## Idea Seeds 示例

1. **跨领域方法迁移** — 1D Tokenizer 方法是否可引入 Unified 框架？
2. **效率-质量平衡点** — 本周 efficient + quality 论文共现，统一框架可能？
3. **趋势捕捉** — `generation` 热度显著上升，是否有切入点？

## 已知限制

- Idea seeds 为启发式生成，需研究者判断可行性
- 趋势计算需要历史数据（第一周因无历史数据，delta 全为正值）
- 追踪关键词列表 TRACKED_KEYWORDS 需随领域演进手动维护
