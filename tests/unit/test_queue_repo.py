#!/usr/bin/env python3
"""
tests/unit/test_queue_repo.py — Unit tests for PaperDB queue CRUD (v3).

All tests use a temp-file DB (not :memory:) because PaperDB creates a new
sqlite3 connection per call, which is incompatible with in-memory DBs.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from paper_db import PaperDB


class QueueTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.db = PaperDB(self.db_path)

    def tearDown(self):
        os.unlink(self.db_path)


# ─────────────── enqueue_job ───────────────

class TestEnqueueJob(QueueTestBase):
    def test_enqueue_returns_id(self):
        jid = self.db.enqueue_job("fetch", "2501.00001", "seed")
        self.assertIsNotNone(jid)
        self.assertIsInstance(jid, int)

    def test_enqueue_idempotent_same_dedupe_key(self):
        jid1 = self.db.enqueue_job("fetch", "2501.00001", "seed")
        jid2 = self.db.enqueue_job("fetch", "2501.00001", "seed")
        self.assertEqual(jid1, jid2, "Same dedupe_key should return same id")

    def test_enqueue_different_dedupe_keys(self):
        jid1 = self.db.enqueue_job("fetch", "2501.00001", "seed", dedupe_key="fetch:A")
        jid2 = self.db.enqueue_job("fetch", "2501.00001", "seed", dedupe_key="fetch:B")
        self.assertNotEqual(jid1, jid2)

    def test_enqueue_with_payload(self):
        payload = {"extra": "data", "score": 0.9}
        jid = self.db.enqueue_job("analyse", "2501.99999", "core_cite", payload=payload)
        self.assertIsNotNone(jid)

    def test_enqueue_priority_stored(self):
        self.db.enqueue_job("fetch", "2501.11111", "seed", priority=5)
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT priority FROM queue_jobs WHERE paper_id = '2501.11111'"
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], 5)


# ─────────────── lease_job ───────────────

class TestLeaseJob(QueueTestBase):
    def test_lease_returns_job(self):
        self.db.enqueue_job("fetch", "2501.00001", "seed")
        job = self.db.lease_job("fetch", "worker-1")
        self.assertIsNotNone(job)
        self.assertEqual(job["paper_id"], "2501.00001")
        self.assertEqual(job["status"], "leased")
        self.assertEqual(job["leased_by"], "worker-1")

    def test_lease_empty_queue_returns_none(self):
        job = self.db.lease_job("fetch", "worker-1")
        self.assertIsNone(job)

    def test_lease_respects_priority_order(self):
        self.db.enqueue_job("fetch", "low", "seed", priority=50, dedupe_key="fetch:low")
        self.db.enqueue_job("fetch", "high", "seed", priority=5, dedupe_key="fetch:high")
        job = self.db.lease_job("fetch", "worker-1")
        self.assertEqual(job["paper_id"], "high", "Higher priority (lower number) should come first")

    def test_lease_respects_not_before(self):
        future = (datetime.utcnow() + timedelta(hours=1)).isoformat()
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO queue_jobs (queue_type, paper_id, source, priority, dedupe_key, not_before)"
            " VALUES ('fetch', '2501.future', 'seed', 10, 'fetch:future', ?)",
            [future],
        )
        conn.commit()
        conn.close()
        job = self.db.lease_job("fetch", "worker-1")
        self.assertIsNone(job, "Job with future not_before should not be leased")

    def test_lease_job_not_re_leasable(self):
        self.db.enqueue_job("fetch", "2501.00001", "seed")
        job1 = self.db.lease_job("fetch", "worker-1")
        self.assertIsNotNone(job1)
        job2 = self.db.lease_job("fetch", "worker-2")
        self.assertIsNone(job2, "Already-leased job should not be leased again")


# ─────────────── ack_job ───────────────

class TestAckJob(QueueTestBase):
    def test_ack_marks_done(self):
        jid = self.db.enqueue_job("fetch", "2501.00001", "seed")
        job = self.db.lease_job("fetch", "worker-1")
        self.db.ack_job(job["id"])
        stats = self.db.get_queue_stats()
        self.assertEqual(stats.get("fetch", {}).get("done"), 1)

    def test_ack_writes_queue_run(self):
        jid = self.db.enqueue_job("fetch", "2501.00001", "seed")
        job = self.db.lease_job("fetch", "worker-1")
        self.db.ack_job(job["id"], run_metrics={"latency_ms": 250})
        conn = sqlite3.connect(self.db_path)
        run = conn.execute(
            "SELECT * FROM queue_runs WHERE job_id = ?", [job["id"]]
        ).fetchone()
        conn.close()
        self.assertIsNotNone(run)
        self.assertEqual(run[6], "success")  # outcome column

    def test_ack_nonexistent_job_no_crash(self):
        self.db.ack_job(999999)  # should not raise


# ─────────────── nack_job ───────────────

class TestNackJob(QueueTestBase):
    def _enqueue_and_lease(self, paper_id="2501.99999", max_retries=3):
        self.db.enqueue_job("analyse", paper_id, "seed",
                            dedupe_key=f"analyse:{paper_id}", max_retries=max_retries)
        return self.db.lease_job("analyse", "worker-1")

    def test_nack_first_failure_schedules_retry(self):
        job = self._enqueue_and_lease()
        status = self.db.nack_job(job["id"], "timeout", "TimeoutError")
        self.assertEqual(status, "pending")

    def test_nack_dead_after_max_retries(self):
        job = self._enqueue_and_lease(max_retries=2)
        # First nack
        s = self.db.nack_job(job["id"], "err", backoff_minutes=[0, 0])
        self.assertEqual(s, "pending")
        job = self.db.lease_job("analyse", "worker-1")
        s = self.db.nack_job(job["id"], "err", backoff_minutes=[0, 0])
        self.assertEqual(s, "pending")
        job = self.db.lease_job("analyse", "worker-1")
        s = self.db.nack_job(job["id"], "err", backoff_minutes=[0, 0])
        self.assertEqual(s, "dead")

    def test_nack_writes_queue_run(self):
        job = self._enqueue_and_lease()
        self.db.nack_job(job["id"], "LLM error", "ValueError")
        conn = sqlite3.connect(self.db_path)
        run = conn.execute(
            "SELECT * FROM queue_runs WHERE job_id = ?", [job["id"]]
        ).fetchone()
        conn.close()
        self.assertIsNotNone(run)
        self.assertIn(run[6], ("retry", "dead"))  # outcome

    def test_nack_sets_last_error(self):
        job = self._enqueue_and_lease()
        self.db.nack_job(job["id"], "connection refused", "NetworkError")
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT last_error FROM queue_jobs WHERE id = ?", [job["id"]]
        ).fetchone()
        conn.close()
        self.assertIn("NetworkError", row[0])


# ─────────────── recover_leased ───────────────

class TestRecoverLeased(QueueTestBase):
    def _create_stale_leased(self, paper_id="2501.stale"):
        self.db.enqueue_job("fetch", paper_id, "seed", dedupe_key=f"fetch:{paper_id}")
        self.db.lease_job("fetch", "stale-worker")
        # Backdate leased_at to past
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE queue_jobs SET leased_at = '2020-01-01T00:00:00' WHERE paper_id = ?",
            [paper_id],
        )
        conn.commit()
        conn.close()

    def test_recover_resets_stale_job(self):
        self._create_stale_leased()
        recovered = self.db.recover_leased(lease_timeout_sec=1)
        self.assertEqual(recovered, 1)
        stats = self.db.get_queue_stats()
        self.assertEqual(stats.get("fetch", {}).get("pending"), 1)

    def test_recover_does_not_touch_fresh_lease(self):
        self.db.enqueue_job("fetch", "2501.fresh", "seed", dedupe_key="fetch:fresh")
        self.db.lease_job("fetch", "fresh-worker")
        recovered = self.db.recover_leased(lease_timeout_sec=3600)
        self.assertEqual(recovered, 0)

    def test_recover_returns_count(self):
        self._create_stale_leased("2501.stale1")
        self._create_stale_leased("2501.stale2")
        recovered = self.db.recover_leased(lease_timeout_sec=1)
        self.assertEqual(recovered, 2)


# ─────────────── get_queue_stats ───────────────

class TestGetQueueStats(QueueTestBase):
    def test_empty_stats(self):
        stats = self.db.get_queue_stats()
        self.assertEqual(stats, {})

    def test_stats_after_enqueue(self):
        self.db.enqueue_job("fetch", "2501.00001", "seed", dedupe_key="fetch:1")
        self.db.enqueue_job("fetch", "2501.00002", "seed", dedupe_key="fetch:2")
        self.db.enqueue_job("analyse", "2501.00001", "seed", dedupe_key="analyse:1")
        stats = self.db.get_queue_stats()
        self.assertEqual(stats["fetch"]["pending"], 2)
        self.assertEqual(stats["analyse"]["pending"], 1)

    def test_stats_mixed_statuses(self):
        self.db.enqueue_job("fetch", "2501.00001", "seed", dedupe_key="fetch:1")
        self.db.enqueue_job("fetch", "2501.00002", "seed", dedupe_key="fetch:2")
        job = self.db.lease_job("fetch", "worker-1")
        self.db.ack_job(job["id"])
        stats = self.db.get_queue_stats()
        self.assertEqual(stats["fetch"]["pending"], 1)
        self.assertEqual(stats["fetch"]["done"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
