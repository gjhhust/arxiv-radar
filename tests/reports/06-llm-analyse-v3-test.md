# 06 LLM Analyse V3 Test

- 日期: 2026-03-15
- 目标: 验证 `paper_analyst_v3.py` 的本地可运行链路

## 覆盖项

- `llm_analyse` 配置解析
- `analyse_paper()` 失败后重试 1 次
- `safe_load_json()` 修复未转义双引号
- `PaperDB.update_analysis_status()` / `get_analysis_status()`
- `CITES` 出边驱动的后验证占位逻辑

## 执行命令

```bash
python3 tests/active/test_paper_analyst_v3_smoke.py
python3 scripts/paper_analyst_v3.py
```

## 结果

- `test_paper_analyst_v3_smoke.py`: 通过
- `paper_analyst_v3.py __main__`: 通过

## 备注

- 当前 `sessions_spawn` 仍为 OpenClaw 运行时接口占位；smoke test 通过注入 `fake_spawn_executor` 完成闭环验证。
- 自测过程中会向本地 DB 增加一条 `0000.00000` 的占位论文记录，用于验证状态写回链路。
