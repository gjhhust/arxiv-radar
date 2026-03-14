#!/usr/bin/env python3
"""
tests/unit/test_fetch_queue.py — Unit tests for fetch_queue.py

All S2 API calls are mocked. No network I/O.
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
from fetch_queue import (
    FetchError,
    FetchFatal,
    ANALYSE_SOURCES,
    process_fetch_job,
    run_fetch_batch,
)

# ─────────────────── helpers ───────────────────

def _make_db() -> tuple[PaperDB, str]:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    path = tmp.name
    tmp.close()
    return PaperDB(path), path


def _s2_paper(arxiv_id="2501.00001", title="Test Paper", n_citations=10):
    return {
        "paperId": "abc123",
        "externalIds": {"ArXiv": arxiv_id},
        "title": title,
        "abstract": "Test abstract.",
        "authors": [{"name": "Alice"}, {"name": "Bob"}],
        "year": 2025,
        "publicationDate": "2025-01-01",
        "citationCount": n_citations,
        "referenceCount": 5,
    }


def _s2_ref(arxiv_id="2401.00099", title="Ref Paper"):
    return {
        "paperId": f"ref_{arxiv_id}",
        "externalIds": {"ArXiv": arxiv_id},
        "title": title,
        "authors": [{"name": "Charlie"}],
        "citationCount": 3,
    }


def _make_job(paper_id="2501.00001", source="seed", job_id=1):
    return {
        "id": job_id,
        "paper_id": paper_id,
        "source": source,
        "queue_type": "fetch",
        "leased_by": "test-worker",
        "leased_at": "2025-01-01T00:00:00",
    }


# ─────────────────── process_fetch_job ───────────────────

class TestProcessFetchJob(unittest.TestCase):
    def setUp(self):
        self.db, self.db_path = _make_db()

    def tearDown(self):
        os.unlink(self.db_path)

    @patch("fetch_queue.get_paper")
    @patch("fetch_queue.get_references")
    @patch("fetch_queue.extract_arxiv_id")
    def test_basic_fetch_upserts_paper(self, mock_extract, mock_refs, mock_paper):
        mock_paper.return_value = _s2_paper()
        mock_refs.return_value = []
        mock_extract.return_value = None

        metrics = process_fetch_job(self.db, _make_job(), "test-w")

        self.assertTrue(metrics["s2_found"])
        paper = self.db.get_paper("2501.00001")
        self.assertIsNotNone(paper)
        self.assertEqual(paper["title"], "Test Paper")

    @patch("fetch_queue.get_paper")
    @patch("fetch_queue.get_references")
    @patch("fetch_queue.extract_arxiv_id")
    def test_references_create_edges(self, mock_extract, mock_refs, mock_paper):
        mock_paper.return_value = _s2_paper("2501.00001")
        ref = _s2_ref("2401.00099")
        mock_refs.return_value = [ref]
        mock_extract.return_value = "2401.00099"

        metrics = process_fetch_job(self.db, _make_job("2501.00001"), "test-w")

        self.assertEqual(metrics["edges_written"], 1)
        neighbors = self.db.get_neighbors("2501.00001")
        self.assertEqual(len(neighbors), 1)
        self.assertEqual(neighbors[0]["neighbor_id"], "2401.00099")
        self.assertEqual(neighbors[0]["edge_type"], "CITES")

    @patch("fetch_queue.get_paper")
    @patch("fetch_queue.get_references")
    @patch("fetch_queue.extract_arxiv_id")
    def test_references_enqueued_for_fetch(self, mock_extract, mock_refs, mock_paper):
        mock_paper.return_value = _s2_paper("2501.00001")
        mock_refs.return_value = [_s2_ref("2401.00099")]
        mock_extract.return_value = "2401.00099"

        metrics = process_fetch_job(self.db, _make_job("2501.00001"), "test-w")

        self.assertEqual(metrics["fetch_enqueued"], 1)
        stats = self.db.get_queue_stats()
        self.assertEqual(stats.get("fetch", {}).get("pending"), 1)

    @patch("fetch_queue.get_paper")
    @patch("fetch_queue.get_references")
    def test_seed_source_enqueues_analyse(self, mock_refs, mock_paper):
        mock_paper.return_value = _s2_paper()
        mock_refs.return_value = []

        metrics = process_fetch_job(self.db, _make_job(source="seed"), "test-w")

        self.assertEqual(metrics["analyse_enqueued"], 1)
        stats = self.db.get_queue_stats()
        self.assertIn("analyse", stats)

    @patch("fetch_queue.get_paper")
    @patch("fetch_queue.get_references")
    def test_incremental_source_no_analyse(self, mock_refs, mock_paper):
        mock_paper.return_value = _s2_paper()
        mock_refs.return_value = []

        metrics = process_fetch_job(
            self.db, _make_job(source="incremental"), "test-w"
        )

        self.assertEqual(metrics["analyse_enqueued"], 0)

    @patch("fetch_queue.get_paper")
    def test_s2_404_returns_not_found(self, mock_paper):
        mock_paper.return_value = None  # S2 404

        metrics = process_fetch_job(self.db, _make_job(), "test-w")

        self.assertFalse(metrics["s2_found"])
        self.assertIsNone(self.db.get_paper("2501.00001"))

    @patch("fetch_queue.get_paper")
    def test_s2_exception_raises_fetch_error(self, mock_paper):
        mock_paper.side_effect = Exception("network timeout")

        with self.assertRaises(FetchError):
            process_fetch_job(self.db, _make_job(), "test-w")

    @patch("fetch_queue.get_paper")
    @patch("fetch_queue.get_references")
    @patch("fetch_queue.extract_arxiv_id")
    def test_non_arxiv_references_skipped(self, mock_extract, mock_refs, mock_paper):
        mock_paper.return_value = _s2_paper()
        mock_refs.return_value = [{"paperId": "xyz", "title": "Non-arxiv paper"}]
        mock_extract.return_value = None  # no arxiv ID

        metrics = process_fetch_job(self.db, _make_job(), "test-w")

        self.assertEqual(metrics["edges_written"], 0)
        self.assertEqual(metrics["fetch_enqueued"], 0)

    @patch("fetch_queue.get_paper")
    @patch("fetch_queue.get_references")
    @patch("fetch_queue.extract_arxiv_id")
    def test_duplicate_reference_idempotent(self, mock_extract, mock_refs, mock_paper):
        """Enqueuing same reference twice should not create duplicate queue jobs."""
        mock_paper.return_value = _s2_paper()
        mock_refs.return_value = [_s2_ref("2401.00099"), _s2_ref("2401.00099")]
        mock_extract.return_value = "2401.00099"

        process_fetch_job(self.db, _make_job(), "test-w")

        stats = self.db.get_queue_stats()
        # Should only have 1 fetch job for 2401.00099 (dedupe by dedupe_key)
        self.assertEqual(stats.get("fetch", {}).get("pending"), 1)

    @patch("fetch_queue.get_paper")
    @patch("fetch_queue.get_references")
    def test_metrics_returned(self, mock_refs, mock_paper):
        mock_paper.return_value = _s2_paper(n_citations=42)
        mock_refs.return_value = []

        metrics = process_fetch_job(self.db, _make_job(), "test-w")

        self.assertIn("latency_ms", metrics)
        self.assertIn("paper_id", metrics)
        self.assertIsInstance(metrics["latency_ms"], int)


# ─────────────────── run_fetch_batch ───────────────────

class TestRunFetchBatch(unittest.TestCase):
    def setUp(self):
        self.db, self.db_path = _make_db()

    def tearDown(self):
        os.unlink(self.db_path)

    @patch("fetch_queue.get_paper")
    @patch("fetch_queue.get_references")
    def test_batch_processes_multiple_jobs(self, mock_refs, mock_paper):
        mock_paper.side_effect = [
            _s2_paper("2501.00001", "Paper A"),
            _s2_paper("2501.00002", "Paper B"),
        ]
        mock_refs.return_value = []

        self.db.enqueue_job("fetch", "2501.00001", "seed", dedupe_key="fetch:2501.00001")
        self.db.enqueue_job("fetch", "2501.00002", "seed", dedupe_key="fetch:2501.00002")

        summary = run_fetch_batch(self.db, worker_id="test-w", max_jobs=5)

        self.assertEqual(summary["processed"], 2)
        self.assertEqual(summary["succeeded"], 2)
        self.assertEqual(summary["dead"], 0)

    @patch("fetch_queue.get_paper")
    def test_batch_handles_fetch_error_with_retry(self, mock_paper):
        mock_paper.side_effect = Exception("network error")

        self.db.enqueue_job("fetch", "2501.00001", "seed", dedupe_key="fetch:1",
                             max_retries=1)

        summary = run_fetch_batch(self.db, worker_id="test-w", max_jobs=1)

        self.assertEqual(summary["retried"], 1)
        self.assertEqual(summary["succeeded"], 0)
        stats = self.db.get_queue_stats()
        self.assertIn("pending", stats.get("fetch", {}))  # scheduled for retry

    @patch("fetch_queue.get_paper")
    @patch("fetch_queue.get_references")
    def test_batch_empty_queue_returns_zero(self, mock_refs, mock_paper):
        summary = run_fetch_batch(self.db, worker_id="test-w", max_jobs=5)
        self.assertEqual(summary["processed"], 0)

    @patch("fetch_queue.get_paper")
    @patch("fetch_queue.get_references")
    def test_batch_respects_max_jobs(self, mock_refs, mock_paper):
        mock_paper.return_value = _s2_paper()
        mock_refs.return_value = []

        for i in range(5):
            self.db.enqueue_job("fetch", f"2501.0000{i}", "seed",
                                 dedupe_key=f"fetch:{i}")

        summary = run_fetch_batch(self.db, worker_id="test-w", max_jobs=3)

        self.assertEqual(summary["processed"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
