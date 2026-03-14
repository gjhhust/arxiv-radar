#!/usr/bin/env python3
"""
tests/unit/test_pipeline.py — Unit tests for pipeline.py

fetch/analyse workers are fully mocked. No network, no LLM.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from paper_db import PaperDB
from pipeline import seed_papers, run_pipeline, main


def _make_db() -> tuple[PaperDB, str]:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    path = tmp.name
    tmp.close()
    return PaperDB(path), path


def _empty_summary():
    return {"processed": 0, "succeeded": 0, "retried": 0, "dead": 0, "errors": []}


# ─────────────────── seed_papers ───────────────────

class TestSeedPapers(unittest.TestCase):
    def setUp(self):
        self.db, self.db_path = _make_db()

    def tearDown(self):
        os.unlink(self.db_path)

    def test_seeds_new_papers(self):
        n = seed_papers(self.db, ["2501.00001", "2501.00002"])
        self.assertEqual(n, 2)
        stats = self.db.get_queue_stats()
        self.assertEqual(stats.get("fetch", {}).get("pending"), 2)

    def test_deduplicates_existing(self):
        """Second seed of same ID must not create duplicate queue jobs."""
        seed_papers(self.db, ["2501.00001"])
        seed_papers(self.db, ["2501.00001"])  # second call — idempotent
        stats = self.db.get_queue_stats()
        # Queue should still have exactly 1 pending job
        self.assertEqual(stats.get("fetch", {}).get("pending"), 1)

    def test_skips_empty_strings(self):
        n = seed_papers(self.db, ["", "  ", "2501.00001"])
        self.assertEqual(n, 1)

    def test_respects_source_label(self):
        seed_papers(self.db, ["2501.00001"], source="manual")
        job = self.db.lease_job("fetch", "test-w", 60)
        self.assertEqual(job["source"], "manual")


# ─────────────────── run_pipeline ───────────────────

class TestRunPipeline(unittest.TestCase):
    def setUp(self):
        self.db, self.db_path = _make_db()

    def tearDown(self):
        os.unlink(self.db_path)

    @patch("pipeline.run_fetch_batch")
    @patch("pipeline.run_analyse_batch")
    def test_calls_fetch_then_analyse(self, mock_analyse, mock_fetch):
        mock_fetch.return_value = {**_empty_summary(), "processed": 3, "succeeded": 3}
        mock_analyse.return_value = {**_empty_summary(), "processed": 1, "succeeded": 1}

        result = run_pipeline(self.db, max_fetch=10, max_analyse=3)

        mock_fetch.assert_called_once()
        mock_analyse.assert_called_once()
        self.assertEqual(result["fetch"]["succeeded"], 3)
        self.assertEqual(result["analyse"]["succeeded"], 1)

    @patch("pipeline.run_fetch_batch")
    @patch("pipeline.run_analyse_batch")
    def test_passes_max_jobs(self, mock_analyse, mock_fetch):
        mock_fetch.return_value = _empty_summary()
        mock_analyse.return_value = _empty_summary()

        run_pipeline(self.db, max_fetch=15, max_analyse=7)

        fetch_call = mock_fetch.call_args
        analyse_call = mock_analyse.call_args
        self.assertEqual(fetch_call.kwargs.get("max_jobs") or fetch_call[1].get("max_jobs"), 15)
        self.assertEqual(analyse_call.kwargs.get("max_jobs") or analyse_call[1].get("max_jobs"), 7)

    @patch("pipeline.run_fetch_batch")
    @patch("pipeline.run_analyse_batch")
    def test_returns_queue_stats(self, mock_analyse, mock_fetch):
        mock_fetch.return_value = _empty_summary()
        mock_analyse.return_value = _empty_summary()

        result = run_pipeline(self.db)

        self.assertIn("queue_stats", result)
        self.assertIsInstance(result["queue_stats"], dict)


# ─────────────────── CLI (main) ───────────────────

class TestCLI(unittest.TestCase):
    def setUp(self):
        self.db, self.db_path = _make_db()

    def tearDown(self):
        os.unlink(self.db_path)

    @patch("pipeline.run_fetch_batch")
    @patch("pipeline.run_analyse_batch")
    def test_seed_then_run(self, mock_analyse, mock_fetch):
        mock_fetch.return_value = _empty_summary()
        mock_analyse.return_value = _empty_summary()

        main(["--seed", "2501.00001", "--db", self.db_path,
              "--max-fetch", "5", "--max-analyse", "2"])

        stats = self.db.get_queue_stats()
        # fetch worker was called (even if empty, seed was there)
        mock_fetch.assert_called_once()

    def test_dry_run_shows_stats(self):
        seed_papers(self.db, ["2501.00001"])
        import io
        from unittest.mock import patch as _patch
        with _patch("builtins.print") as mock_print:
            main(["--dry-run", "--db", self.db_path])
        # Should print something about fetch queue
        output = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("fetch", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
