#!/usr/bin/env python3
"""
tests/integration/test_e2e_pipeline.py — End-to-end pipeline smoke test.

Strategy:
  - Fetch phase:   real Semantic Scholar API (network required)
  - Analyse phase: mock LLM (no external LLM calls — deterministic + fast)

Test coverage:
  1. --dry-run CLI: seed enqueues papers, queue stats printed, no processing
  2. Fetch phase:   papers table populated, CITES edges written,
                    discovered refs enqueued into fetch queue,
                    seed papers auto-enqueued into analyse queue
  3. Analyse phase: mock result stored, result_path captured in job metrics,
                    core_cite IDs re-enqueued into fetch queue
  4. Full pipeline: seed → run_pipeline → verifies all invariants together

Network guard: all tests that require S2 API are skipped automatically if
`https://api.semanticscholar.org` is not reachable (e.g. CI with no network).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from paper_db import PaperDB
from fetch_queue import run_fetch_batch
from analyse_queue import run_analyse_batch
from pipeline import seed_papers, run_pipeline

# ─────────────────────────────────────────────────────────────────
# Network availability check
# ─────────────────────────────────────────────────────────────────

def _s2_reachable() -> bool:
    """Return True if Semantic Scholar API responds (fast probe)."""
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://api.semanticscholar.org/graph/v1/paper/2406.07550"
            "?fields=title",
            headers={"User-Agent": "arxiv-radar-test/1.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status == 200
    except Exception:
        return False


NETWORK_AVAILABLE = _s2_reachable()
skip_no_network = unittest.skipUnless(
    NETWORK_AVAILABLE,
    "Semantic Scholar API not reachable — skipping integration tests",
)

# ─────────────────────────────────────────────────────────────────
# Well-known stable arxiv IDs used across tests
# 2406.07550 — Mamba (state space model), ~2024, heavily cited
# 2302.13971 — LLaMA, very stable S2 entry
# ─────────────────────────────────────────────────────────────────
SEED_ID_PRIMARY = "2406.07550"
SEED_ID_SECONDARY = "2302.13971"

# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _make_db() -> tuple[PaperDB, str]:
    """Create a fresh temporary PaperDB; caller is responsible for cleanup."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    path = tmp.name
    tmp.close()
    return PaperDB(path), path


def _mock_analyse_result(paper_id: str, result_dir: Path) -> dict:
    """Write a fake analysis JSON and return a result dict."""
    result_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "arxiv_id": paper_id,
        "title": "Mock Paper Title",
        "cn_abstract": "这是一篇测试论文。",
        "score": 8,
        "core_cite": [
            {"arxiv_id": "2312.00001", "title": "Core Cite Paper 1"},
            {"arxiv_id": "2312.00002", "title": "Core Cite Paper 2"},
        ],
        "keywords": ["test", "mock"],
        "analysis_result_path": str(result_dir / f"{paper_id}.json"),
    }
    result_path = result_dir / f"{paper_id}.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False))
    result["_result_path"] = str(result_path)
    return result


# ─────────────────────────────────────────────────────────────────
# Test: --dry-run CLI
# ─────────────────────────────────────────────────────────────────

class TestDryRunCLI(unittest.TestCase):
    """Verify `pipeline.py --seed <id> --dry-run` output."""

    def test_dry_run_prints_stats_and_no_processing(self):
        """Dry run seeds the queue and prints stats without fetching."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "pipeline.py"),
                 "--seed", SEED_ID_PRIMARY,
                 "--db", db_path,
                 "--dry-run"],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0,
                             msg=f"pipeline.py exited non-zero:\n{result.stderr}")
            output = result.stdout
            # Must mention "Seeded" and "Queue stats"
            self.assertIn("Seeded", output, f"Expected 'Seeded' in output:\n{output}")
            self.assertIn("Queue stats", output.replace("queue_stats", "Queue stats") or output,
                          f"Expected queue stats in output:\n{output}")
            # Must NOT contain "Pipeline complete" (no processing happened)
            self.assertNotIn("Pipeline complete", output,
                             "Dry run should not run the pipeline")
            # The DB must have a pending fetch job
            db = PaperDB(db_path)
            stats = db.get_queue_stats()
            fetch_stats = stats.get("fetch", {})
            self.assertGreater(
                fetch_stats.get("pending", 0), 0,
                f"Expected pending fetch job after seed; got stats={stats}",
            )
        finally:
            os.unlink(db_path)


# ─────────────────────────────────────────────────────────────────
# Test: Fetch phase (real S2)
# ─────────────────────────────────────────────────────────────────

@skip_no_network
class TestFetchPhase(unittest.TestCase):
    """Fetch one paper via real S2; verify DB + queue invariants."""

    def setUp(self):
        self.db, self.db_path = _make_db()

    def tearDown(self):
        os.unlink(self.db_path)

    def test_fetch_populates_papers_table(self):
        """After fetch, paper should be in the papers table with title."""
        seed_papers(self.db, [SEED_ID_PRIMARY], source="seed")
        summary = run_fetch_batch(self.db, max_jobs=1)

        self.assertGreaterEqual(summary["succeeded"], 1,
                                f"Fetch failed: {summary['errors']}")
        paper = self.db.get_paper(SEED_ID_PRIMARY)
        self.assertIsNotNone(paper, "Paper not found in DB after fetch")
        self.assertTrue(paper.get("title"), "Paper has empty title after fetch")
        # S2 title should contain something meaningful
        self.assertGreater(len(paper["title"]), 5, f"Title too short: {paper['title']!r}")

    def test_fetch_writes_cites_edges(self):
        """Fetch should write CITES edges for references."""
        seed_papers(self.db, [SEED_ID_PRIMARY], source="seed")
        run_fetch_batch(self.db, max_jobs=1)

        edge_count = self.db.count_edges("CITES")
        self.assertGreater(edge_count, 0,
                           "No CITES edges written after fetch — S2 refs may be empty")

    def test_fetch_enqueues_discovered_refs(self):
        """Discovered references should be enqueued into fetch queue."""
        seed_papers(self.db, [SEED_ID_PRIMARY], source="seed")
        run_fetch_batch(self.db, max_jobs=1)

        stats = self.db.get_queue_stats()
        fetch_stats = stats.get("fetch", {})
        # There should be pending discovered-ref fetch jobs
        # (Some will be pending, original seed job is done)
        total_fetch = (
            fetch_stats.get("pending", 0)
            + fetch_stats.get("done", 0)
            + fetch_stats.get("failed", 0)
        )
        self.assertGreater(total_fetch, 1,
                           f"Expected > 1 total fetch jobs (seed + refs); got {fetch_stats}")

    def test_fetch_seed_auto_enqueues_for_analyse(self):
        """Seed-sourced papers should be auto-enqueued into analyse queue."""
        seed_papers(self.db, [SEED_ID_PRIMARY], source="seed")
        run_fetch_batch(self.db, max_jobs=1)

        stats = self.db.get_queue_stats()
        analyse_stats = stats.get("analyse", {})
        self.assertGreater(
            analyse_stats.get("pending", 0), 0,
            f"Expected pending analyse job after seed fetch; got {analyse_stats}",
        )


# ─────────────────────────────────────────────────────────────────
# Test: Analyse phase (mock LLM)
# ─────────────────────────────────────────────────────────────────

class TestAnalysePhase(unittest.TestCase):
    """Analyse phase with mock LLM — verifies result_path + core_cite re-queue."""

    def setUp(self):
        self.db, self.db_path = _make_db()
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        os.unlink(self.db_path)
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _seed_and_fake_fetch(self, paper_id: str) -> None:
        """Insert a paper into DB and enqueue it for analysis directly."""
        self.db.upsert_paper({
            "id": paper_id,
            "title": "Mock Fetched Paper",
            "abstract": "Abstract for mock paper.",
            "source": "seed",
        })
        self.db.enqueue_job(
            queue_type="analyse",
            paper_id=paper_id,
            source="seed",
            priority=10,
            dedupe_key=f"analyse:{paper_id}",
        )

    def test_analyse_records_result_path(self):
        """analyse_paper mock result path should be stored in job metrics."""
        paper_id = "9999.00001"
        self._seed_and_fake_fetch(paper_id)

        result_dir = Path(self.tmp_dir)
        mock_result = _mock_analyse_result(paper_id, result_dir)

        with patch("analyse_queue.analyse_paper", return_value=mock_result):
            summary = run_analyse_batch(self.db, max_jobs=1)

        self.assertEqual(summary["succeeded"], 1,
                         f"Analyse failed: {summary['errors']}")

        # Verify the job run recorded result_path in metrics
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT metrics FROM queue_runs WHERE outcome='success' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()

        self.assertIsNotNone(row, "No successful queue_run found")
        metrics = json.loads(row["metrics"] or "{}")
        self.assertIn("result_path", metrics,
                      f"result_path not in run metrics: {metrics}")
        self.assertTrue(metrics["result_path"],
                        "result_path is empty in run metrics")

    def test_analyse_core_cite_enqueues_fetch_jobs(self):
        """core_cite entries in analysis result should be enqueued for fetch."""
        paper_id = "9999.00002"
        self._seed_and_fake_fetch(paper_id)

        result_dir = Path(self.tmp_dir)
        mock_result = _mock_analyse_result(paper_id, result_dir)
        core_cite_ids = [c["arxiv_id"] for c in mock_result["core_cite"]]

        with patch("analyse_queue.analyse_paper", return_value=mock_result):
            run_analyse_batch(self.db, max_jobs=1)

        stats = self.db.get_queue_stats()
        fetch_stats = stats.get("fetch", {})
        # core_cite IDs ("2312.00001", "2312.00002") should be pending fetch jobs
        self.assertGreaterEqual(
            fetch_stats.get("pending", 0), len(core_cite_ids),
            f"Expected {len(core_cite_ids)} pending fetch jobs for core_cite; got {fetch_stats}",
        )

    def test_analyse_fatal_on_missing_paper(self):
        """Analyse job for paper not in DB should dead-letter immediately."""
        self.db.enqueue_job(
            queue_type="analyse",
            paper_id="nonexistent.99999",
            source="seed",
            priority=10,
            dedupe_key="analyse:nonexistent.99999",
        )
        summary = run_analyse_batch(self.db, max_jobs=1)
        self.assertEqual(summary["dead"], 1,
                         f"Expected dead=1, got {summary}")
        self.assertEqual(summary["succeeded"], 0)


# ─────────────────────────────────────────────────────────────────
# Test: Full pipeline (real S2 fetch + mock analyse)
# ─────────────────────────────────────────────────────────────────

@skip_no_network
class TestFullPipeline(unittest.TestCase):
    """Full pipeline: seed → fetch (real S2) → analyse (mock LLM)."""

    def setUp(self):
        self.db, self.db_path = _make_db()
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        os.unlink(self.db_path)
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_full_pipeline_seed_to_analyse(self):
        """Seed one paper → run_pipeline → all invariants pass."""
        seed_papers(self.db, [SEED_ID_PRIMARY], source="seed")

        result_dir = Path(self.tmp_dir)

        def mock_analyse(paper_id, title, **kwargs):
            return _mock_analyse_result(paper_id, result_dir)

        with patch("analyse_queue.analyse_paper", side_effect=mock_analyse):
            result = run_pipeline(
                self.db,
                max_fetch=1,
                max_analyse=1,
                worker_id="e2e-test",
            )

        # Fetch succeeded
        self.assertGreaterEqual(result["fetch"]["succeeded"], 1,
                                f"Fetch errors: {result['fetch']['errors']}")

        # Analyse succeeded
        self.assertGreaterEqual(result["analyse"]["succeeded"], 1,
                                f"Analyse errors: {result['analyse']['errors']}")

        # Paper is in DB with a title
        paper = self.db.get_paper(SEED_ID_PRIMARY)
        self.assertIsNotNone(paper, "Paper not found in DB after full pipeline")
        self.assertTrue(paper.get("title"), "Paper title is empty")

        # At least some CITES edges were written
        edge_count = self.db.count_edges("CITES")
        self.assertGreater(edge_count, 0, "No CITES edges after full pipeline")

        # Discovered papers are in fetch queue (pending or done)
        stats = result["queue_stats"]
        fetch_stats = stats.get("fetch", {})
        total_fetch = sum(fetch_stats.values())
        self.assertGreater(total_fetch, 1,
                           f"Expected multiple fetch queue entries; got {fetch_stats}")


# ─────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s — %(message)s")
    unittest.main(verbosity=2)
