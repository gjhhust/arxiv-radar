---
name: arxiv-radar
description: "Daily arxiv CV research paper tracker. Crawls arxiv daily, semantically filters papers by domain, labels VIP authors and open-source papers, generates a beautiful daily briefing. Set up with cron for fully automated daily reports. Configurable via markdown config file."
---

# arxiv-radar — Daily CV Research Briefing

Automated daily arxiv paper tracker for computer vision researchers. Crawls arxiv, filters by semantic similarity to your research domains, labels notable authors and open-source work, and generates a curated daily report.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Test run (dry-run, 20 papers)
python scripts/main.py --dry-run --max 20

# Full run for yesterday
python scripts/main.py

# Specific date
python scripts/main.py --date 2024-06-01
```

## Config

Edit `config.template.md` (or copy it to `~/arxiv-radar-config.md`):

- **Natural language sections**: describe your research interests
- **Parameter table**: adjust thresholds, top-K, categories
- **VIP authors**: add researchers to watch
- **Domains**: add seed papers for new research areas

## Cron Setup (Daily 8 AM)

```bash
# Add to crontab (crontab -e)
0 8 * * * cd /path/to/arxiv-radar && python scripts/main.py >> ~/arxiv-daily/cron.log 2>&1
```

Or via OpenClaw cron:

```bash
openclaw cron add \
  --name "arxiv daily report" \
  --cron "0 8 * * *" \
  --session isolated \
  --message "Run the arxiv-radar pipeline: cd ~/.openclaw/workspace/skills/arxiv-radar && python scripts/main.py" \
  --announce \
  --channel discord
```

## Architecture

```
config.template.md
      ↓
config_parser.py  ──→  config dict
      ↓
crawler.py        ──→  [paper, paper, ...]   (arxiv API)
      ↓
labeler.py        ──→  [paper+labels, ...]   (VIP, open-source, labs)
      ↓
filter.py         ──→  {domain_0: [...], domain_1: [...]}  (semantic similarity)
      ↓
recommender.py    ──→  {domain_0: {recommendations: [...]}, ...}
      ↓
reporter.py       ──→  markdown report string
      ↓
save / Discord / stdout
```

## Modules

| Module | File | Description |
|--------|------|-------------|
| Config | `scripts/config_parser.py` | Parse markdown config |
| Crawler | `scripts/crawler.py` | Fetch arxiv papers |
| Labeler | `scripts/labeler.py` | VIP/open-source labels |
| Filter | `scripts/filter.py` | Semantic similarity filter |
| Recommender | `scripts/recommender.py` | Must-read selection |
| Reporter | `scripts/reporter.py` | Report generation |
| Main | `scripts/main.py` | Full pipeline runner |

## Key Parameters

| Param | Default | Effect |
|-------|---------|--------|
| `similarity_threshold` | 0.35 | Floor cutoff. Raise to 0.40-0.45 for stricter filtering |
| `adaptive_top_k` | 30 | Max papers per domain per day |
| `threshold_mode` | adaptive | `fixed`/`adaptive`/`hybrid` |
| `embedding_model` | all-MiniLM-L6-v2 | Faster but less accurate; use all-mpnet-base-v2 for higher accuracy |

## Extending

**Add a new domain**: Add a seed paper abstract to `data/seeds/domain3_xxx.txt` and a domain section to `config.template.md`.

**Add VIP authors**: Edit the VIP authors section in `config.template.md`.

**Custom noise keywords**: Edit the noise filter section in config.

**LLM why-read**: Set `use_llm_why_read: true` in config and set `OPENAI_API_KEY` environment variable for AI-generated reading rationale.

## Dependencies

- `arxiv` — paper fetching
- `sentence-transformers` — semantic embedding
- `thefuzz` — fuzzy author name matching
- `numpy` — vector math
