# arxiv-radar 🔬

> 面向 CV 研究者的 arXiv 每日追踪系统，支持日报 / 周报 / 月报，输出到 Obsidian vault。

## 特性

- 语义相似度过滤（sentence-transformers，Apple Silicon MPS 加速）
- VIP 作者检测（fuzzy match）+ 研究组识别
- 论文类型分类：📝 方法文 / 📊 Benchmark / 🔬 Survey
- 中文摘要 + 一句话通俗解释（via Claude 子 agent）
- Obsidian 友好输出：frontmatter + wiki-links
- 日报 / 周报 / 月报全覆盖

## 安装

```bash
pip install -r requirements.txt
```

## 快速开始

```bash
# 日报
python3 scripts/main.py --date 2026-03-11

# 周报
python3 scripts/weekly.py --start 2026-03-01 --end 2026-03-07 \
  --output-dir ~/mydata/notes/科研追踪/周报

# 月报
python3 scripts/monthly.py --year 2026 --month 3
```

## 配置

复制 `config.template.md` 为 `config.md`，编辑领域、VIP 作者、输出路径等。

## 目录结构

```
arxiv-radar/
├── scripts/          # 核心模块
│   ├── crawler.py    # arXiv 爬取
│   ├── filter.py     # 语义过滤
│   ├── labeler.py    # 标签检测
│   ├── analyzer.py   # 中文摘要（sub-agent）
│   ├── recommender.py
│   ├── reporter.py   # 日报
│   ├── weekly.py     # 周报
│   ├── monthly.py    # 月报
│   ├── aggregator.py # 多日聚合
│   └── main.py       # CLI 入口
├── data/
│   └── seeds/        # 领域种子文件
├── tests/            # 测试 & 测试报告
├── config.template.md
├── SKILL.md
├── TUNING_NOTES.md
└── requirements.txt
```

## Roadmap

见 [ROADMAP.md](./ROADMAP.md) 或 [Taskr board](https://taskr.one)

## License

MIT
