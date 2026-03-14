# PHASE6_INTEGRATION_PROPOSAL.md
# Phase 6: main.py & reporter.py Integration Plan

> **Status**: ⚠️ Pending guojiahao confirmation before applying  
> 作成者: Mox | 2026-03-15 06:44 CST

---

## What's already done (no confirmation needed)

| File | Status | Tests |
|------|--------|-------|
| `scripts/post_process.py` | ✅ New file, committed | 21 unit tests |
| `scripts/reporter_v3_enrich.py` | ✅ New file, committed | 32 unit tests |

---

## Change 1: `scripts/main.py` — add post_process step

**Location**: after line 142 (the existing `DB update skipped` warning block)

**Exact change** — add the following block after `except Exception as e: logger.warning(...)`:

```python
        # ── 7c. Sync v3 analysis results → DB ──
        cache_dir = SKILL_DIR / "data" / "cache" / "analysis_v3"
        if cache_dir.is_dir():
            try:
                from post_process import run_post_process
                pp_stats = run_post_process(db, cache_dir=cache_dir)
                if pp_stats["processed"] > 0:
                    logger.info(f"🔬 v3 post-process: {pp_stats}")
            except Exception as e:
                logger.warning(f"v3 post-process skipped: {e}")
```

**Context** (lines 128-155 of current main.py):
```python
    if not dry_run:
        saved_path = save_report(report, config, str(target_date))
        if saved_path:
            logger.info(f"✅ Report saved to: {saved_path}")

        # ── 7b. Update knowledge graph with today's papers ──
        db_path = SKILL_DIR / "data" / "paper_network.db"
        if db_path.exists():
            try:
                from paper_db import PaperDB
                from context_injector import update_db_from_daily
                db = PaperDB(db_path)
                relevant = filter_result.get("filtered_papers", [])
                if relevant:
                    db_stats = update_db_from_daily(relevant, db)
                    logger.info(f"📊 DB updated: {db_stats}")
            except Exception as e:
                logger.warning(f"DB update skipped: {e}")
            # ← INSERT 7c BLOCK HERE

    else:
        ...
```

---

## Change 2: `scripts/reporter.py` — add v3 enrichment section

**Location**: in `generate_report()`, after `_render_must_reads(...)` is called

**Exact change** — find `report = header + must_reads + full_pool` (or equivalent)
and append a v3 section:

```python
    # Optional: v3 enrichment section
    v3_section = ""
    try:
        from reporter_v3_enrich import append_v3_section_to_report
        from paper_db import PaperDB
        db_path = Path(__file__).parent.parent / "data" / "paper_network.db"
        if db_path.exists():
            db = PaperDB(db_path)
            must_read_papers = []
            for rec_list in recommendations.values():
                for rec in rec_list.get("recommendations", []):
                    must_read_papers.append(rec["paper"])
            v3_section = "\n" + append_v3_section_to_report("", must_read_papers, db=db)
    except Exception:
        pass  # v3 enrichment is best-effort

    report = header + must_reads + full_pool + v3_section
```

**Note**: The exact integration point depends on the `generate_report()` function body.
Check line ~230 of reporter.py where the sections are assembled.

---

## How to apply

Once guojiahao confirms, Mox will:
1. Apply Change 1 to `main.py` (10 lines)
2. Read the exact assembly point in `reporter.py` and apply Change 2
3. Run integration smoke test: `python3 scripts/pipeline.py --seed 2406.07550 --dry-run`
4. Commit: `feat(report): integrate v3 pipeline into daily/weekly report`
5. Mark Phase 6 complete in PROJECT_STATE.md

---

## Risk assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| `post_process` import fails | Low | Wrapped in try/except, daily run continues |
| reporter.py assembly point differs | Medium | Will read exact code before patching |
| DB not initialized | Low | `db_path.exists()` guard |

Both changes are **non-breaking** — wrapped in try/except, v3 enrichment is best-effort.
