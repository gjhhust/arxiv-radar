#!/usr/bin/env python3
"""Smoke test for paper_analyst_v3 integration."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from config_parser import parse_config
from paper_analyst_v3 import analyse_paper
from paper_db import PaperDB


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        db_path = tmp / "paper_network.db"
        prompt_path = tmp / "prompt_H3_template.txt"
        prompt_path.write_text("H3 smoke prompt", encoding="utf-8")

        config_path = tmp / "config.md"
        config_path.write_text(
            "\n".join(
                [
                    "## LLM Analyse (v3)",
                    "llm_analyse:",
                    "  enabled: true",
                    "  default_model: wq/minimaxm25",
                    "  fallback_model: wq/glm5",
                    "  max_retries: 1",
                    "  timeout_seconds: 60",
                    f"  prompt_template: {prompt_path}",
                ]
            ),
            encoding="utf-8",
        )

        parsed = parse_config(config_path)
        assert parsed["llm_analyse"]["fallback_model"] == "wq/glm5"

        malformed_result = tmp / "agent_result.json"
        malformed_result.write_text(
            '{\n'
            '  "cn_oneliner": "uses "bad quotes" safely",\n'
            '  "cn_abstract": "smoke test",\n'
            '  "contribution_type": "incremental",\n'
            '  "editorial_note": "[前驱] x [贡献] y [判断] z",\n'
            '  "why_read": "read it",\n'
            '  "method_variants": [{"base_method":"titok","variant_tag":"titok:smoke","description":"desc"}],\n'
            '  "core_cite": [{"title":"Known Ref","role":"extends","note":"match"}]\n'
            '}',
            encoding="utf-8",
        )

        attempts = {"count": 0}

        def fake_spawn_executor(**_: object) -> dict[str, str]:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("transient spawn failure")
            return {
                "session_id": "smoke-session",
                "result_path": str(malformed_result),
                "transcript_path": str(tmp / "transcript.json"),
            }

        db = PaperDB(db_path)
        db.upsert_paper({"id": "2501.00001", "title": "Smoke Paper", "authors": [], "source": "manual"})
        db.upsert_paper({"id": "ref-1", "title": "Known Ref", "authors": [], "source": "manual"})
        db.add_edge("2501.00001", "ref-1", "CITES")

        result = analyse_paper(
            "2501.00001",
            "Smoke Paper",
            db=db,
            config_path=config_path,
            spawn_executor=fake_spawn_executor,
        )

        status = db.get_analysis_status("2501.00001")
        assert attempts["count"] == 2
        assert result["_verified"] is True
        assert result["_matched"] == ["Known Ref"]
        assert status["analysis_status"] == "completed"
        assert status["analysis_session_id"] == "smoke-session"
        assert Path(status["analysis_result_path"]).exists()

        print(
            json.dumps(
                {
                    "attempts": attempts["count"],
                    "analysis_status": status["analysis_status"],
                    "matched": result["_matched"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
