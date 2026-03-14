# Tests

arxiv-radar 测试目录。所有测试按阶段组织，旧阶段归档，当前测试在 `active/`。

---

## 当前测试（active/）

### comparison_v3 — H vs I-a 公平对比（Phase 1）

**目标：** 控制所有变量，仅改变引用列表获取方式，评估 H（预嵌入）vs I-a（agent 自查）的幻觉率差异。

**状态：** 🔄 任务文件已生成，待 spawn sessions

**运行：**
```bash
python3 tests/active/comparison_v3/run_comparison_v2.py --step build    # 已完成
python3 tests/active/comparison_v3/run_comparison_v2.py --step verify   # 待执行
python3 tests/active/comparison_v3/run_comparison_v2.py --step report
```

---

## 历史测试（archive/）

### 01_prompt_variants — Prompt A/B/C/D/E/F 变体测试

**结论：** Variant F 定稿为生产 prompt（三段 editorial_note，结构化 core_cite）  
**文件：** `prompts/variant_f_production.md`

### 02_e2e_test — E2E 全流程测试 v2

**结论：** 11 papers × 3 models（33 cells），minimaxm25 稳定，gpt52 引用最佳

### 03_gh_hallucination — G/H 幻觉率测试

**结论：**
- G/H Test v1（2026-03-13）：结论无效（bib 缺失导致高误报）
- G/H Test v2（2026-03-14）：H（S2 锚点）= 100% pass rate，G（无锚点）= 69%，锚点策略有效

---

## 模块测试报告（reports/）

各生产模块的单元测试报告，按编号归档。
