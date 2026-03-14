#!/usr/bin/env python3
"""
tests/unit/test_analyse_queue.py — Unit tests for analyse_queue.py

analyse_paper() is fully mocked. No LLM calls, no network I/O.
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
from analyse_queue import (
    AnalyseError,
    AnalyseFatal,
    _extract_core_cite_ids,
    process_analyse_job,
    run_analyse_batch,
)

# ─────────────────── helpers ───────────────────

def _make_db() -> tuple[PaperDB, str]:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    path = tmp.name
    tmp.close()
    return PaperDB(path), path


def _seed_paper(db: PaperDB, paper_id="2501.00001", title="Test Paper"):
    db.upsert_paper({
        "id": paper_id,
        "title": title,
        "abstract": "Test abstract.",
        "authors": ["Alice"],
        "source": "seed",
    })


def _make_job(paper_id="2501.00001", source="seed", job_id=1):
    return {
        "id": job_id,
        "paper_id": paper_id,
        "source": source,
        "queue_type": "analyse",
        "leased_by": "test-worker",
        "leased_at": "2025-01-01T00:00:00",
    }


def _mock_result(arxiv_id="2501.00001", core_cite=None):
    return {
        "arxiv_id": arxiv_id,
        "title": "Test Paper",
        "analysis_result_path": f"/tmp/{arxiv_id}_result.json",
        "analysis_model": "wq/minimaxm25",
        "core_cite": core_cite or [],
        "_verified": True,
    }


# ─────────────────── _extract_core_cite_ids ───────────────────

class TestExtractCoreCiteIds(unittest.TestCase):
    def test_extracts_arxiv_id_field(self):
        result = {"core_cite": [{"title": "A", "arxiv_id": "2401.00001"}]}
        self.assertEqual(_extract_core_cite_ids(result), ["2401.00001"])

    def test_extracts_id_field_fallback(self):
        result = {"core_cite": [{"title": "B", "id": "2401.00002"}]}
        self.assertEqual(_extract_core_cite_ids(result), ["2401.00002"])

    def test_skips_http_ids(self):
        result = {"core_cite": [{"title": "C", "arxiv_id": "https://arxiv.org/abs/2401.00003"}]}
        self.assertEqual(_extract_core_cite_ids(result), [])

    def test_skips_empty_ids(self):
        result = {"core_cite": [{"title": "D", "arxiv_id": ""}]}
        self.assertEqual(_extract_core_cite_ids(result), [])

    def test_empty_core_cite(self):
        result = {"core_cite": []}
        self.assertEqual(_extract_core_cite_ids(result), [])

    def test_missing_core_cite_key(self):
        result = {}
        self.assertEqual(_extract_core_cite_ids(result), [])


# ─────────────────── process_analyse_job ───────────────────

class TestProcessAnalyseJob(unittest.TestCase):
    def setUp(self):
        self.db, self.db_path = _make_db()
        _seed_paper(self.db)

    def tearDown(self):
        os.unlink(self.db_path)

    @patch("analyse_queue.analyse_paper")
    def test_basic_success_returns_metrics(self, mock_analyse):
        mock_analyse.return_value = _mock_result()

        metrics = process_analyse_job(self.db, _make_job(), "test-w")

        self.assertEqual(metrics["paper_id"], "2501.00001")
        self.assertIn("result_path", metrics)
        self.assertIsInstance(metrics["latency_ms"], int)
        self.assertEqual(metrics["core_cite_found"], 0)
        self.assertEqual(metrics["fetch_enqueued"], 0)

    @patch("analyse_queue.analyse_paper")
    def test_core_cite_enqueues_fetch_jobs(self, mock_analyse):
        mock_analyse.return_value = _mock_result(
            core_cite=[
                {"title": "Paper A", "arxiv_id": "2401.00001"},
                {"title": "Paper B", "arxiv_id": "2401.00002"},
            ]
        )

        metrics = process_analyse_job(self.db, _make_job(), "test-w")

        self.assertEqual(metrics["core_cite_found"], 2)
        self.assertEqual(metrics["fetch_enqueued"], 2)
        stats = self.db.get_queue_stats()
        self.assertEqual(stats.get("fetch", {}).get("pending"), 2)

    @patch("analyse_queue.analyse_paper")
    def test_core_cite_deduplication(self, mock_analyse):
        """Same core_cite paper enqueued twice should only create one job."""
        mock_analyse.return_value = _mock_result(
            core_cite=[
                {"title": "Same", "arxiv_id": "2401.00001"},
                {"title": "Same", "arxiv_id": "2401.00001"},
            ]
        )

        process_analyse_job(self.db, _make_job(), "test-w")

        stats = self.db.get_queue_stats()
        self.assertEqual(stats.get("fetch", {}).get("pending"), 1)

    def test_paper_not_in_db_raises_fatal(self):
        job = _make_job(paper_id="9999.99999")

        with self.assertRaises(AnalyseFatal):
            process_analyse_job(self.db, job, "test-w")

    @patch("analyse_queue.analyse_paper")
    def test_paper_empty_title_raises_fatal(self, mock_analyse):
        self.db.upsert_paper({"id": "2501.99999", "title": "", "source": "seed"})
        job = _make_job(paper_id="2501.99999")

        with self.assertRaises(AnalyseFatal):
            process_analyse_job(self.db, job, "test-w")

    @patch("analyse_queue.analyse_paper")
    def test_analysis_error_raises_analyse_error(self, mock_analyse):
        from analyse_queue import AnalysisError
        mock_analyse.side_effect = AnalysisError("LLM timeout")

        with self.assertRaises(AnalyseError):
            process_analyse_job(self.db, _make_job(), "test-w")

    @patch("analyse_queue.analyse_paper")
    def test_config_disabled_raises_fatal(self, mock_analyse):
        from analyse_queue import AnalysisError
        mock_analyse.side_effect = AnalysisError("llm_analyse is disabled in config")

        with self.assertRaises(AnalyseFatal):
            process_analyse_job(self.db, _make_job(), "test-w")

    @patch("analyse_queue.analyse_paper")
    def test_spawn_error_raises_analyse_error(self, mock_analyse):
        from analyse_queue import SpawnError
        mock_analyse.side_effect = SpawnError("agent spawn timeout")

        with self.assertRaises(AnalyseError):
            process_analyse_job(self.db, _make_job(), "test-w")

    @patch("analyse_queue.analyse_paper")
    def test_result_path_captured(self, mock_analyse):
        mock_analyse.return_value = _mock_result()

        metrics = process_analyse_job(self.db, _make_job(), "test-w")

        self.assertIn("2501.00001", metrics["result_path"])


# ─────────────────── run_analyse_batch ───────────────────

class TestRunAnalyseBatch(unittest.TestCase):
    def setUp(self):
        self.db, self.db_path = _make_db()

    def tearDown(self):
        os.unlink(self.db_path)

    @patch("analyse_queue.analyse_paper")
    def test_batch_processes_multiple_jobs(self, mock_analyse):
        mock_analyse.side_effect = [
            _mock_result("2501.00001"),
            _mock_result("2501.00002"),
        ]
        _seed_paper(self.db, "2501.00001", "Paper A")
        _seed_paper(self.db, "2501.00002", "Paper B")
        self.db.enqueue_job("analyse", "2501.00001", "seed", dedupe_key="analyse:1")
        self.db.enqueue_job("analyse", "2501.00002", "seed", dedupe_key="analyse:2")

        summary = run_analyse_batch(self.db, worker_id="test-w", max_jobs=5)

        self.assertEqual(summary["processed"], 2)
        self.assertEqual(summary["succeeded"], 2)
        self.assertEqual(summary["dead"], 0)

    @patch("analyse_queue.analyse_paper")
    def test_batch_handles_retryable_error(self, mock_analyse):
        from analyse_queue import AnalysisError
        mock_analyse.side_effect = AnalysisError("LLM timeout")
        _seed_paper(self.db)
        self.db.enqueue_job("analyse", "2501.00001", "seed",
                             dedupe_key="analyse:1", max_retries=1)

        summary = run_analyse_batch(self.db, worker_id="test-w", max_jobs=1)

        self.assertEqual(summary["retried"], 1)
        self.assertEqual(summary["succeeded"], 0)

    @patch("analyse_queue.analyse_paper")
    def test_batch_respects_max_jobs(self, mock_analyse):
        mock_analyse.return_value = _mock_result()
        for i in range(5):
            _seed_paper(self.db, f"2501.0000{i}", f"Paper {i}")
            self.db.enqueue_job("analyse", f"2501.0000{i}", "seed",
                                 dedupe_key=f"analyse:{i}")

        summary = run_analyse_batch(self.db, worker_id="test-w", max_jobs=3)

        self.assertEqual(summary["processed"], 3)

    @patch("analyse_queue.analyse_paper")
    def test_batch_empty_queue(self, mock_analyse):
        summary = run_analyse_batch(self.db, worker_id="test-w", max_jobs=5)
        self.assertEqual(summary["processed"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
