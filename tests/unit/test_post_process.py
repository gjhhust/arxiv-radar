#!/usr/bin/env python3
"""
tests/unit/test_post_process.py — Unit tests for post_process.py

No network or LLM calls. All data is synthetic.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from paper_db import PaperDB, EDGE_EXTENDS, EDGE_CITES
from post_process import (
    _load_result,
    _role_to_edge_type,
    process_result,
    run_post_process,
)


# ─────────────────── helpers ───────────────────

def _make_db() -> tuple[PaperDB, str]:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    path = tmp.name
    tmp.close()
    return PaperDB(path), path


def _sample_result(
    arxiv_id: str = "2501.00001",
    title: str = "Test Paper",
    core_cite: list | None = None,
    method_variants: list | None = None,
    contribution_type: str = "incremental",
) -> dict:
    """Build a sample analysis result dict."""
    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "cn_oneliner": "这是一篇测试论文的中文摘要",
        "cn_abstract": "详细的中文摘要内容。",
        "contribution_type": contribution_type,
        "editorial_note": "[前驱] base. [贡献] improve. [判断] ok.",
        "why_read": "Useful for testing.",
        "keywords": ["test", "mock"],
        "core_cite": core_cite or [],
        "method_variants": method_variants or [],
        "analysis_model": "wq/minimaxm25",
        "analysis_session_id": "test-session",
        "analysis_result_path": "/tmp/fake_result.json",
    }


def _write_result_json(cache_dir: Path, result: dict, filename: str = None) -> Path:
    """Write a result dict as JSON into the cache dir."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    fname = filename or f"{result['arxiv_id']}.json"
    path = cache_dir / fname
    path.write_text(json.dumps(result, ensure_ascii=False))
    return path


# ─────────────────── _role_to_edge_type ───────────────────

class TestRoleToEdgeType(unittest.TestCase):

    def test_extends_roles(self):
        for role in ("extends", "Extends", "EXTENDS", "builds_on", "improves"):
            self.assertEqual(_role_to_edge_type(role), EDGE_EXTENDS, f"role={role}")

    def test_cites_role_returns_none(self):
        """CITES edges are handled by fetch_queue, so role='cites' → None."""
        self.assertIsNone(_role_to_edge_type("cites"))

    def test_empty_role_returns_none(self):
        self.assertIsNone(_role_to_edge_type(""))
        self.assertIsNone(_role_to_edge_type(None))

    def test_unknown_role_returns_none(self):
        self.assertIsNone(_role_to_edge_type("something_else"))


# ─────────────────── _load_result ───────────────────

class TestLoadResult(unittest.TestCase):

    def test_valid_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write_result_json(Path(d), _sample_result())
            result = _load_result(path)
            self.assertIsNotNone(result)
            self.assertEqual(result["arxiv_id"], "2501.00001")

    def test_missing_arxiv_id_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            bad = {"title": "No ID"}
            p = Path(d) / "bad.json"
            p.write_text(json.dumps(bad))
            self.assertIsNone(_load_result(p))

    def test_invalid_json_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "garbage.json"
            p.write_text("{not valid json")
            self.assertIsNone(_load_result(p))


# ─────────────────── process_result ───────────────────

class TestProcessResult(unittest.TestCase):

    def setUp(self):
        self.db, self.db_path = _make_db()
        # Insert a paper to process against
        self.db.upsert_paper({
            "id": "2501.00001",
            "title": "Test Paper",
            "source": "seed",
        })

    def tearDown(self):
        os.unlink(self.db_path)

    def test_updates_paper_fields(self):
        result = _sample_result()
        metrics = process_result(self.db, result)
        self.assertTrue(metrics["fields_updated"])

        paper = self.db.get_paper("2501.00001")
        self.assertEqual(paper["cn_oneliner"], "这是一篇测试论文的中文摘要")
        self.assertEqual(paper["paper_type"], "incremental")

    def test_sets_analysis_status_completed(self):
        result = _sample_result()
        metrics = process_result(self.db, result)
        self.assertTrue(metrics["status_set"])

        status = self.db.get_analysis_status("2501.00001")
        self.assertEqual(status["analysis_status"], "completed")
        self.assertEqual(status["analysis_model"], "wq/minimaxm25")

    def test_writes_extends_edge(self):
        result = _sample_result(
            core_cite=[
                {"arxiv_id": "2312.00001", "title": "Base Paper", "role": "extends", "note": "key"},
            ]
        )
        metrics = process_result(self.db, result)
        self.assertEqual(metrics["extends_edges"], 1)

        edges = self.db.count_edges(EDGE_EXTENDS)
        self.assertEqual(edges, 1)

    def test_skips_cites_role_edge(self):
        """role='cites' should NOT create a new edge (fetch_queue handles it)."""
        result = _sample_result(
            core_cite=[
                {"arxiv_id": "2312.00002", "title": "Cited Paper", "role": "cites"},
            ]
        )
        metrics = process_result(self.db, result)
        self.assertEqual(metrics["extends_edges"], 0)

    def test_stores_method_variants(self):
        result = _sample_result(
            method_variants=[
                {"base_method": "titok", "variant_tag": "titok:v2", "description": "improved"},
            ]
        )
        metrics = process_result(self.db, result)
        self.assertGreaterEqual(metrics["method_variants"], 1)

    def test_dry_run_no_writes(self):
        result = _sample_result(
            core_cite=[
                {"arxiv_id": "2312.00003", "role": "extends"},
            ]
        )
        metrics = process_result(self.db, result, dry_run=True)
        self.assertFalse(metrics["fields_updated"])
        self.assertFalse(metrics["status_set"])
        self.assertEqual(metrics["extends_edges"], 0)

        # Paper should NOT have been updated
        paper = self.db.get_paper("2501.00001")
        self.assertIsNone(paper.get("cn_oneliner") or None)

    def test_keywords_stored_as_json(self):
        result = _sample_result()
        process_result(self.db, result)
        paper = self.db.get_paper("2501.00001")
        kw = json.loads(paper.get("keywords", "[]"))
        self.assertIn("test", kw)
        self.assertIn("mock", kw)


# ─────────────────── run_post_process (batch) ───────────────────

class TestRunPostProcess(unittest.TestCase):

    def setUp(self):
        self.db, self.db_path = _make_db()
        self.cache_dir = Path(tempfile.mkdtemp())
        # Insert papers that analysis results will reference
        self.db.upsert_paper({"id": "2501.00001", "title": "Paper A", "source": "seed"})
        self.db.upsert_paper({"id": "2501.00002", "title": "Paper B", "source": "seed"})

    def tearDown(self):
        os.unlink(self.db_path)
        import shutil
        shutil.rmtree(self.cache_dir, ignore_errors=True)

    def test_processes_all_results(self):
        _write_result_json(self.cache_dir, _sample_result("2501.00001"))
        _write_result_json(self.cache_dir, _sample_result("2501.00002", title="Paper B"))

        summary = run_post_process(self.db, self.cache_dir)
        self.assertEqual(summary["processed"], 2)
        self.assertEqual(summary["updated"], 2)

    def test_skips_paper_not_in_db(self):
        _write_result_json(self.cache_dir, _sample_result("9999.99999", title="Ghost"))

        summary = run_post_process(self.db, self.cache_dir)
        self.assertEqual(summary["processed"], 0)
        self.assertEqual(summary["skipped"], 1)

    def test_skips_already_completed(self):
        _write_result_json(self.cache_dir, _sample_result("2501.00001"))

        # First run
        run_post_process(self.db, self.cache_dir)
        # Second run — should skip
        summary = run_post_process(self.db, self.cache_dir)
        self.assertEqual(summary["processed"], 0)
        self.assertEqual(summary["skipped"], 1)

    def test_filter_by_paper_id(self):
        _write_result_json(self.cache_dir, _sample_result("2501.00001"))
        _write_result_json(self.cache_dir, _sample_result("2501.00002"))

        summary = run_post_process(self.db, self.cache_dir, paper_id="2501.00001")
        self.assertEqual(summary["processed"], 1)

    def test_empty_cache_dir(self):
        summary = run_post_process(self.db, self.cache_dir)
        self.assertEqual(summary["processed"], 0)
        self.assertEqual(summary["skipped"], 0)

    def test_missing_cache_dir(self):
        summary = run_post_process(self.db, Path("/nonexistent/dir"))
        self.assertEqual(summary["processed"], 0)

    def test_invalid_json_counted_as_skipped(self):
        bad = self.cache_dir / "broken.json"
        bad.write_text("{invalid json!!")

        summary = run_post_process(self.db, self.cache_dir)
        self.assertEqual(summary["skipped"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
