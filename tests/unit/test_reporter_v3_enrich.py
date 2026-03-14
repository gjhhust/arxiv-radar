#!/usr/bin/env python3
"""
tests/unit/test_reporter_v3_enrich.py — Unit tests for reporter_v3_enrich.py

All DB and file I/O is mocked or uses temp files.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from reporter_v3_enrich import (
    _safe_json_list,
    _format_core_cite,
    _render_v3_paper_block,
    _read_analysis_json,
    enrich_paper_with_v3,
    enrich_papers_with_v3,
    render_v3_section,
    append_v3_section_to_report,
)


# ─────────────────── helpers ───────────────────

def _paper(paper_id: str = "2501.00001", **kwargs) -> dict:
    base = {
        "id": paper_id,
        "title": "Test Paper Title",
        "arxiv_url": f"https://arxiv.org/abs/{paper_id}",
        "best_score": 0.75,
        "abstract": "Abstract text.",
    }
    base.update(kwargs)
    return base


def _db_row(paper_id: str = "2501.00001", **kwargs) -> dict:
    base = {
        "id": paper_id,
        "title": "Test Paper Title",
        "cn_oneliner": "测试论文中文摘要",
        "cn_abstract": "详细中文摘要。",
        "paper_type": "incremental",
        "keywords": json.dumps(["vision", "transformer"]),
        "analysis_status": "completed",
        "analysis_result_path": "",
        "analysis_model": "wq/minimaxm25",
        "analysis_date": "2026-03-15T00:00:00Z",
    }
    base.update(kwargs)
    return base


def _mock_db(paper_id: str = "2501.00001", **db_row_kwargs) -> MagicMock:
    db = MagicMock()
    db.get_paper.return_value = _db_row(paper_id, **db_row_kwargs)
    return db


# ─────────────────── _safe_json_list ───────────────────

class TestSafeJsonList(unittest.TestCase):

    def test_list_passthrough(self):
        self.assertEqual(_safe_json_list(["a", "b"]), ["a", "b"])

    def test_json_string(self):
        self.assertEqual(_safe_json_list('["a", "b"]'), ["a", "b"])

    def test_empty_string(self):
        self.assertEqual(_safe_json_list(""), [])

    def test_none(self):
        self.assertEqual(_safe_json_list(None), [])

    def test_invalid_json(self):
        self.assertEqual(_safe_json_list("{bad json"), [])

    def test_non_list_json(self):
        self.assertEqual(_safe_json_list('{"key": "val"}'), [])


# ─────────────────── _format_core_cite ───────────────────

class TestFormatCoreCite(unittest.TestCase):

    def test_empty(self):
        self.assertEqual(_format_core_cite([]), "")

    def test_with_arxiv_id(self):
        cc = [{"title": "Base Paper", "arxiv_id": "2312.00001", "role": "extends"}]
        result = _format_core_cite(cc)
        self.assertIn("Base Paper", result)
        self.assertIn("2312.00001", result)
        self.assertIn("extends", result)

    def test_without_arxiv_id(self):
        cc = [{"title": "Just Title", "role": "cites"}]
        result = _format_core_cite(cc)
        self.assertIn("Just Title", result)

    def test_max_items_truncated(self):
        cc = [{"title": f"Paper {i}", "arxiv_id": f"2312.0000{i}"} for i in range(5)]
        result = _format_core_cite(cc, max_items=2)
        self.assertIn("+ 3 more", result)

    def test_exactly_max_items_no_suffix(self):
        cc = [{"title": f"Paper {i}", "arxiv_id": f"2312.0000{i}"} for i in range(3)]
        result = _format_core_cite(cc, max_items=3)
        self.assertNotIn("more", result)


# ─────────────────── _read_analysis_json ───────────────────

class TestReadAnalysisJson(unittest.TestCase):

    def test_returns_none_for_empty_path(self):
        self.assertIsNone(_read_analysis_json(""))
        self.assertIsNone(_read_analysis_json(None))

    def test_returns_none_for_missing_file(self):
        self.assertIsNone(_read_analysis_json("/nonexistent/path.json"))

    def test_reads_valid_json(self):
        data = {"arxiv_id": "2501.00001", "cn_oneliner": "test"}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = f.name
        try:
            result = _read_analysis_json(path)
            self.assertEqual(result["cn_oneliner"], "test")
        finally:
            os.unlink(path)

    def test_returns_none_for_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{invalid!!")
            path = f.name
        try:
            self.assertIsNone(_read_analysis_json(path))
        finally:
            os.unlink(path)


# ─────────────────── enrich_paper_with_v3 ───────────────────

class TestEnrichPaperWithV3(unittest.TestCase):

    def test_no_db_returns_unchanged(self):
        paper = _paper()
        enriched = enrich_paper_with_v3(paper, db=None)
        self.assertEqual(enriched, paper)

    def test_enriches_cn_oneliner(self):
        paper = _paper()
        db = _mock_db()
        enriched = enrich_paper_with_v3(paper, db=db)
        self.assertEqual(enriched["cn_oneliner"], "测试论文中文摘要")

    def test_enriches_paper_type(self):
        paper = _paper()
        db = _mock_db()
        enriched = enrich_paper_with_v3(paper, db=db)
        self.assertEqual(enriched["paper_type"], "incremental")

    def test_enriches_analysis_status(self):
        paper = _paper()
        db = _mock_db()
        enriched = enrich_paper_with_v3(paper, db=db)
        self.assertEqual(enriched["analysis_status"], "completed")

    def test_does_not_overwrite_existing_field(self):
        """Existing paper fields should NOT be overwritten by DB fields."""
        paper = _paper(cn_oneliner="EXISTING VALUE")
        db = _mock_db()
        enriched = enrich_paper_with_v3(paper, db=db)
        self.assertEqual(enriched["cn_oneliner"], "EXISTING VALUE")

    def test_enriches_from_result_json(self):
        """If result JSON exists, pull why_read and core_cite."""
        result_data = {
            "why_read": "Very useful paper",
            "core_cite": [{"title": "Base", "arxiv_id": "2312.00001", "role": "extends"}],
            "editorial_note": "[前驱] X [贡献] Y",
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(result_data, f)
            result_path = f.name

        try:
            paper = _paper()
            db = _mock_db(analysis_result_path=result_path)
            enriched = enrich_paper_with_v3(paper, db=db)
            self.assertEqual(enriched["why_read"], "Very useful paper")
            self.assertEqual(len(enriched["core_cite"]), 1)
            self.assertEqual(enriched["editorial_note"], "[前驱] X [贡献] Y")
        finally:
            os.unlink(result_path)

    def test_paper_not_in_db_returns_unchanged(self):
        paper = _paper()
        db = MagicMock()
        db.get_paper.return_value = None
        enriched = enrich_paper_with_v3(paper, db=db)
        self.assertEqual(enriched, paper)

    def test_does_not_mutate_original(self):
        paper = _paper()
        original_keys = set(paper.keys())
        db = _mock_db()
        enrich_paper_with_v3(paper, db=db)
        # Original should be unchanged
        self.assertEqual(set(paper.keys()), original_keys)


# ─────────────────── render_v3_section ───────────────────

class TestRenderV3Section(unittest.TestCase):

    def _enriched_paper(self, paper_id: str, **kwargs) -> dict:
        return {
            "id": paper_id,
            "title": f"Paper {paper_id}",
            "arxiv_url": f"https://arxiv.org/abs/{paper_id}",
            "cn_oneliner": "中文摘要",
            "paper_type": "方法文",
            "analysis_status": "completed",
            "keywords": ["vision", "test"],
            "best_score": 0.8,
            **kwargs,
        }

    def test_empty_papers_returns_empty(self):
        self.assertEqual(render_v3_section([]), "")

    def test_contains_title(self):
        papers = [self._enriched_paper("2501.00001")]
        section = render_v3_section(papers)
        self.assertIn("Deep Analysis", section)

    def test_contains_paper_title(self):
        papers = [self._enriched_paper("2501.00001")]
        section = render_v3_section(papers)
        self.assertIn("Paper 2501.00001", section)

    def test_contains_cn_oneliner(self):
        papers = [self._enriched_paper("2501.00001")]
        section = render_v3_section(papers)
        self.assertIn("中文摘要", section)

    def test_analysed_only_filter(self):
        """analysed_only=True should exclude pending papers."""
        papers = [
            self._enriched_paper("2501.00001", analysis_status="completed"),
            self._enriched_paper("2501.00002", analysis_status="pending"),
        ]
        section = render_v3_section(papers, analysed_only=True)
        self.assertIn("2501.00001", section)
        self.assertNotIn("2501.00002", section)

    def test_max_papers_respected(self):
        papers = [self._enriched_paper(f"2501.0000{i}") for i in range(10)]
        section = render_v3_section(papers, max_papers=3)
        # Only 3 papers should appear
        count = sum(1 for p in papers if p["title"] in section)
        self.assertLessEqual(count, 3)

    def test_analysed_papers_appear_first(self):
        papers = [
            self._enriched_paper("2501.00001", analysis_status="pending", best_score=0.9),
            self._enriched_paper("2501.00002", analysis_status="completed", best_score=0.5),
        ]
        section = render_v3_section(papers)
        pos_completed = section.find("2501.00002")
        pos_pending = section.find("2501.00001")
        self.assertLess(pos_completed, pos_pending,
                        "completed paper should appear before pending")


# ─────────────────── append_v3_section_to_report ───────────────────

class TestAppendV3SectionToReport(unittest.TestCase):

    def test_appends_section_to_report(self):
        base_report = "# Daily Report\n\nSome content."
        papers = [{
            "id": "2501.00001",
            "title": "Test",
            "arxiv_url": "https://arxiv.org/abs/2501.00001",
            "analysis_status": "completed",
            "cn_oneliner": "中文",
            "best_score": 0.8,
        }]
        result = append_v3_section_to_report(base_report, papers, db=None)
        self.assertIn("Daily Report", result)
        self.assertIn("Deep Analysis", result)

    def test_no_papers_returns_original(self):
        base_report = "# Daily Report\n\nSome content."
        result = append_v3_section_to_report(base_report, [], db=None)
        self.assertEqual(result, base_report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
