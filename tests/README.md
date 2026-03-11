# Tests

每个功能模块独立测试后生成测试报告，存放于 `tests/reports/`。

## 结构

```
tests/
├── README.md
├── reports/          # 测试报告（Markdown）
│   ├── 01-crawler-test.md
│   ├── 02-filter-test.md
│   └── ...
├── test_crawler.py
├── test_filter.py
├── test_labeler.py
├── test_analyzer.py
└── fixtures/         # 测试用固定数据
    └── sample_papers.json
```

## 测试规范

1. 每个模块有独立的 `test_<module>.py`
2. 新功能先写测试，通过后写测试报告 `tests/reports/XX-<feature>-test.md`
3. 测试报告包含：测试目标、测试数据、通过情况、发现的问题
4. PR 前必须所有测试通过

## 运行

```bash
python3 -m pytest tests/ -v
# 或单独
python3 tests/test_crawler.py
```
