# P2 Baseline 提取 + 方法线构建 — 测试报告

**测试日期**: 2026-03-11  
**分支**: `feature/p2-p5-pkn-core`

## 测试结果

| # | 测试 | 状态 | 备注 |
|---|------|------|------|
| 2.1 | 已知方法名扫描（CANONICAL_METHODS 字典） | ✅ | 37 个方法/33 别名 |
| 2.2 | extract_baselines_keyword 三篇论文 | ✅ | 每篇识别 5-7 个 baseline |
| 2.3 | normalize_method_name 标准化 | ✅ | "vqvae"→"vq-vae", "llava-1.5"→"llava" |
| 2.4 | extract_extends_keyword | ✅ | "chameleon", "llava" 从 Wallaroo 正确抽出 |
| 2.5 | build_compares_with_edges (共享 baseline → 边) | ✅ | TiTok 出现 2 篇 → COMPARES_WITH 边 |
| 2.6 | process_papers 完整 P2 pipeline | ✅ | 3篇: 21 baselines, 2 COMPARES_WITH edges |

## 详细输出（3 篇测试论文）

- Beyond Language Modeling: vqgan, titok, blip-3, llava, clip (7 baselines)
- CaTok: vq-vae, vqgan, titok, llamagen, var (7 baselines)
- Wallaroo: dit, blip-3, llava, show-o, janus (7 baselines)；extends: chameleon, llava

## 已知限制

- `extract_extends_keyword` 会误识别 "the" 等词（正则 boundary 不够精确）
- 新论文名（如 2026 年新方法）需手动添加到 CANONICAL_METHODS 字典
- LLM 辅助模式 (use_llm=True) 需 claude CLI 或 OPENAI_API_KEY
