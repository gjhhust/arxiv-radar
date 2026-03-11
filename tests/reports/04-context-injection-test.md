# P4 Context 注入 + 增量每日更新 — 测试报告

**测试日期**: 2026-03-11  
**分支**: `feature/p2-p5-pkn-core`

## 测试结果

| # | 测试 | 状态 | 备注 |
|---|------|------|------|
| 4.1 | get_context_for_paper: 引用前驱检索 | ✅ | TiTok 正确作为"引用前驱"出现 |
| 4.2 | get_context_for_paper: 同 Baseline 方法线 | ✅ | CaTok/BLM 因共享 TiTok baseline 出现 |
| 4.3 | build_enriched_prompt: context 注入到 prompt | ✅ | 210 chars context + original prompt |
| 4.4 | enrich_weekly_analysis: 批量注入 | ✅ | 修改 paper['graph_context'] 字段 |
| 4.5 | 无 DB 时优雅降级（返回空字符串） | ✅ | context=None → 跳过注入 |

## Context 格式示例

**Beyond Language Modeling** 的注入上下文：
```
### 📚 领域背景（知识图谱注入）
• [引用前驱] TiTok: An Image is Worth 32 Tokens (2024-06)
  32个token实现图像重建与生成
• [同 Baseline 方法线] CaTok: Causal Image Tokenization (2026-03)
  mean flow实现1D因果图像编码
```

## Weekly 接入

- weekly.py 在分析前自动尝试加载 `data/paper_network.db`
- 若 DB 存在，为每篇 highlight 论文注入图谱上下文
- 图谱上下文仅对 **[必读]** 论文展示，避免正文过长

## 已知限制

- 首次运行前需先完成 P3 领域扩散（DB 才有数据）
- 日增量 update_db_from_daily 每次最多处理 20 篇新论文的 S2 调用（控制 API 成本）
