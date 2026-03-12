# arxiv-radar v3.0 — LLM Schema Test Plan

_This file is the single source of truth for the A/B test. Do NOT drift from it._
_Last updated: 2026-03-12_

---

## Background

We are testing two competing schemes for LLM-based paper analysis,
to determine the best approach before committing to a schema migration.

Both schemes target the same two researcher use cases:
1. **Per-paper contextual analysis** — rich editorial judgment + method lineage
2. **Field evolution tree** — temporal method branch visualization (Obsidian graph)

---

## Two Schemes

### Scheme A: "Specialist Pipeline"
**Philosophy:** Keep modular multi-call structure, enhance each specialist.

```
paper + abstract
     │
     ├── [Call 1] analyzer.py         → cn_abstract, cn_oneliner
     ├── [Call 2] reference_ranker.py → method_variants, contribution_type,
     │                                  editorial_note, citation_stance per ref
     └── (keyword) baseline_extractor → baselines (no extra LLM call)
```

**Prompts:** Two focused prompts, each small and fast.
**Failure mode:** If Call 2 fails, still have basic summary from Call 1.
**New fields produced:** `contribution_type`, `editorial_note`, `citation_stance`

---

### Scheme B: "Unified Analyst"
**Philosophy:** One comprehensive LLM call with full context.

```
paper + abstract + top 15 references (title + 150-char abstract)
     │
     └── [Call 1] paper_analyst.py → cn_abstract, cn_oneliner,
                                      contribution_type, editorial_note,
                                      method_variants, key_refs (with stance)
```

**Prompts:** One large, context-rich prompt.
**Failure mode:** All-or-nothing. Fallback to template summaries.
**New fields produced:** All at once, context-aware citation stance.

---

## New Fields (both schemes must produce the same output schema)

| Field | Type | Description |
|-------|------|-------------|
| `contribution_type` | ENUM TEXT | `incremental` / `significant` / `story-heavy` / `foundational` |
| `editorial_note` | TEXT | 1-2句编辑判断（方法评价，含跨域感知） |
| `why_read` | TEXT | 1句推荐理由（值不值得读，为什么） |
| `citation_stance` | JSON in paper_edges.metadata | per-ref: `extends/contrasts/uses/supports/mentions` |

Fields NOT in this test (deferred):
- `key_claims` (exact metrics — hallucination risk too high)
- `method_lineage` table (derived from method_variants, no LLM needed)
- `concept_timeline` table (trend.py output, no LLM needed)

---

## Test Papers

10 papers selected from DB with rich reference data (out_cites > 10):

```
2603.06449  CaTok: Taming Mean Flows for One-Dimensional Causal Image Tokenization
2603.06577  Omni-Diffusion: Unified Multimodal Understanding and Generation
2603.07192  FastSTAR: Spatiotemporal Token Pruning for Efficient Autoregressive Video Synthesis
2603.07057  SODA: Sensitivity-Oriented Dynamic Acceleration for Diffusion Transformer
2603.06985  Perception-Aware Multimodal Spatial Reasoning from Monocular Images
2603.05800  StreamWise: Serving Multi-Modal Generation in Real-Time at Scale
2603.06932  HIERAMP: Coarse-to-Fine Autoregressive Amplification for Dataset Distillation
2603.05438  Planning in 8 Tokens: A Compact Discrete Tokenizer for Latent World Model
2603.08064  Evaluating Generative Models via One-Dimensional Code Distributions
2603.09086  Latent World Models for Automated Driving: Unified Taxonomy
```

---

## LLM Models to Test

All 7 wq backend models:

| Alias | Model ID |
|-------|----------|
| claude46 | wq/claude46 |
| claude45 | wq/claude45 |
| glm5 | wq/glm5 |
| katcoder | wq/katcoder |
| kimik25 | wq/kimik25 |
| minimaxm21 | wq/minimaxm21 |
| minimaxm25 | wq/minimaxm25 |

---

## Test Runner: scripts/test_schemes.py

### Usage
```bash
# Run full test (all schemes × all models × all papers)
python3 scripts/test_schemes.py --all

# Run single scheme
python3 scripts/test_schemes.py --scheme A --models claude46,glm5

# Quick smoke test (2 papers, 2 models)
python3 scripts/test_schemes.py --smoke

# Summarize results (no new LLM calls)
python3 scripts/test_schemes.py --report
```

### Output Structure
```
data/test_results/
├── scheme_a/
│   ├── claude46/
│   │   ├── 2603.06449.json   ← raw LLM output
│   │   └── ...
│   ├── glm5/
│   └── ...
├── scheme_b/
│   └── (same structure)
├── summary.md                ← human-readable comparison table
└── evaluation.json           ← structured metrics
```

### Evaluation Metrics (auto-computed)
1. **Fill rate** — % of expected fields present and non-empty
2. **Contribution type validity** — is it one of the 4 ENUM values?
3. **Editorial note length** — 20-150 chars (too short = useless, too long = noise)
4. **Method variant format** — matches `base:variant` pattern
5. **Citation stance coverage** — % of key_refs with valid stance
6. **Parse success rate** — % of calls that returned valid JSON
7. **Latency** — seconds per paper

---

## Obsidian Visualization: scripts/obsidian_writer_v2.py

### What it generates
Two types of notes:

**1. Paper notes** (one per paper, enhanced from v1):
```markdown
---
id: 2603.06449
title: CaTok
contribution_type: incremental
editorial_note: "双向→因果注意力，causal tokenizer首次入CV"
method_variants: [titok:causal-rewrite, flow-matching:image-tokenization]
---

> 💡 **CaTok** — 32个token实现因果图像tokenization

**贡献类型:** `incremental` | **推荐:** why_read here

**方法谱系:** [[titok]] · [[flow-matching]]

**关系:**
- [[TiTok]] — `extends` 直接继承并修改
- [[VQGAN]] — `uses` 沿用码本设计
- [[LlamaGen]] — `contrasts` AR生成对比baseline
```

**2. Method hub notes** (one per base_method, NEW):
```markdown
---
type: method-hub
method: titok
paper_count: N
---

# TiTok — 方法谱系

## 变体论文
- [[CaTok]] `titok:causal-rewrite` (2026-03)
- [[Paper B]] `titok:semantic-alignment` (2025-11)
```

Method hub notes are what makes Obsidian graph view show the tree structure —
papers link TO their method hubs, hubs link to each other via parent methods.

### Usage
```bash
python3 scripts/obsidian_writer_v2.py --output ~/mydata/notes/arxiv-radar/ --batch 50
```

---

## Report Output: daily/weekly enhanced format

Both schemes should produce enough data for an enriched report section.
Enhanced `reporter.py` reads new fields and adds:
- `contribution_type` badge in daily brief
- `editorial_note` as "Mox says:" annotation
- Method hub links in weekly trend section

Test this via:
```bash
python3 scripts/reporter.py --test-output data/test_results/report_preview.md
```

---

## Git Workflow

Branch: `feat/v3-llm-schema-test`

Commits:
- `feat: add contribution_type, editorial_note, why_read fields (migration)`
- `feat: scheme-a enhanced specialist pipeline`
- `feat: scheme-b unified paper_analyst`
- `feat: test_schemes runner + evaluation metrics`
- `feat: obsidian_writer_v2 with method hub notes`
- `feat: reporter enhanced with new fields`
- `test: run full A/B test results`

After user approval of winning scheme:
- `refactor: remove losing scheme, finalize winning approach`
- `feat: migrate production DB to new schema`
- Tag: `v3.0`

---

## Decision Criteria (for guojiahao to evaluate)

After test results are in, evaluate:

1. **Parse success rate** > 90% required for both schemes
2. **editorial_note quality** — does it say something a researcher couldn't tell from the title?
3. **contribution_type accuracy** — does the categorization feel right to you?
4. **citation_stance usefulness** — does knowing "this extends TiTok" vs "this contrasts TiTok" change how you read the analysis?
5. **Cost/speed tradeoff** — Scheme A (2 calls, faster) vs Scheme B (1 call but larger)
6. **Obsidian graph** — does the method hub structure create a useful visual graph?

Winning scheme gets merged to main and becomes the production pipeline.

---

## Status Tracking

| Task | Status |
|------|--------|
| TEST_PLAN.md created | ✅ 2026-03-12 |
| DB migration (new columns) | ⬜ |
| Scheme A implementation | ⬜ |
| Scheme B implementation (paper_analyst.py) | ⬜ |
| test_schemes.py runner | ⬜ |
| obsidian_writer_v2.py | ⬜ |
| reporter.py enhanced | ⬜ |
| Full test run | ⬜ |
| summary.md generated | ⬜ |
| User review & approval | ⬜ |
| Production migration | ⬜ |
