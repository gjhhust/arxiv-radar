"""
paper_analyst_v3.py — Production H3 analyst integration entrypoint.

This module integrates the H3-tested paper-analyst flow into arxiv-radar:
- stable JSON loading with GLM-style repair
- retry and fallback model selection
- DB status tracking
- post-verification hook against S2 references

OpenClaw note:
- The real `sessions_spawn` call must run inside an OpenClaw-capable runtime.
- This module exposes a placeholder interface and allows dependency injection
  for tests or future runtime wiring.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from config_parser import parse_config
from paper_db import EDGE_CITES, PaperDB

logger = logging.getLogger(__name__)

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = SKILL_DIR / "config.template.md"
DEFAULT_RESULTS_DIR = SKILL_DIR / "data" / "cache" / "analysis_v3"


class AnalysisError(RuntimeError):
    """Raised when paper analysis fails after retries."""


class SpawnError(RuntimeError):
    """Raised when the paper-analyst agent cannot be spawned."""


def fix_json_llm_output(content: str) -> str:
    """Repair common unescaped quote issues in LLM-generated JSON."""
    lines = content.split("\n")
    fixed_lines = []

    for line in lines:
        if '": "' not in line:
            fixed_lines.append(line)
            continue
        match = re.match(r'^(\s*"[^"]+"\s*:\s*")(.*)$', line)
        if not match:
            fixed_lines.append(line)
            continue

        prefix = match.group(1)
        rest = match.group(2)

        if rest.rstrip().endswith('",'):
            value = rest[:-2]
            suffix = '",'
        elif rest.rstrip().endswith('"'):
            value = rest[:-1]
            suffix = '"'
        else:
            fixed_lines.append(line)
            continue

        fixed_value = re.sub(r'(?<!\\)"', r'\\"', value)
        fixed_lines.append(prefix + fixed_value + suffix)

    return "\n".join(fixed_lines)


def safe_load_json(file_path: str | Path) -> dict[str, Any]:
    """Load JSON with a repair pass for malformed LLM output."""
    path = Path(file_path)
    content = path.read_text(encoding="utf-8")

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        fixed_content = fix_json_llm_output(content)
        try:
            data = json.loads(fixed_content)
        except json.JSONDecodeError as exc:
            raise AnalysisError(f"Failed to parse JSON result: {path}") from exc

        backup_path = path.with_suffix(path.suffix + ".original")
        shutil.copy(path, backup_path)
        path.write_text(fixed_content, encoding="utf-8")
        logger.warning("Repaired malformed JSON output: %s", path)
        return data


def _load_runtime_config(config_path: str | Path | None = None) -> dict[str, Any]:
    cfg_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    config = parse_config(cfg_path)
    return config["llm_analyse"]


def _ensure_paper_stub(db: PaperDB, arxiv_id: str, title: str) -> None:
    if db.get_paper(arxiv_id):
        return
    db.upsert_paper({"id": arxiv_id, "title": title, "authors": [], "source": "manual"})
    logger.info("Inserted paper stub for analysis target: %s", arxiv_id)


def build_analysis_task(arxiv_id: str, title: str, prompt_template: str | Path) -> str:
    """Build the task content that will be sent to paper-analyst."""
    prompt_path = Path(prompt_template)
    if not prompt_path.is_absolute():
        prompt_path = SKILL_DIR / prompt_path
    prompt_text = prompt_path.read_text(encoding="utf-8")
    return (
        f"{prompt_text}\n\n"
        f"请分析以下论文：\n"
        f"- arXiv ID: {arxiv_id}\n"
        f"- Title: {title}\n\n"
        "仅返回结果 JSON 文件的绝对路径。"
    )


def sessions_spawn(*, agent_id: str, model: str, task: str, timeout_seconds: int) -> dict[str, Any]:
    """
    Placeholder for OpenClaw sessions_spawn integration.

    Runtime wiring options:
    - Inject `spawn_executor` into `analyse_paper`
    - Or set `ARXIV_RADAR_ANALYST_RESULT_PATH` during local smoke tests
    """
    result_path = os.environ.get("ARXIV_RADAR_ANALYST_RESULT_PATH")
    session_id = os.environ.get("ARXIV_RADAR_ANALYST_SESSION_ID", "local-placeholder")
    transcript = os.environ.get("ARXIV_RADAR_ANALYST_TRANSCRIPT")
    if result_path:
        logger.info("Using local placeholder sessions_spawn result: %s", result_path)
        return {
            "session_id": session_id,
            "result_path": result_path,
            "transcript_path": transcript,
            "agent_id": agent_id,
            "model": model,
            "timeout_seconds": timeout_seconds,
            "task_preview": task[:120],
        }
    raise SpawnError("sessions_spawn is not available outside OpenClaw runtime")


def spawn_analyst(
    arxiv_id: str,
    title: str,
    model: str,
    timeout_seconds: int,
    prompt_template: str | Path,
    spawn_executor: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Spawn the paper-analyst agent and return spawn metadata."""
    task = build_analysis_task(arxiv_id, title, prompt_template)
    executor = spawn_executor or sessions_spawn
    payload = executor(
        agent_id="paper-analyst",
        model=model,
        task=task,
        timeout_seconds=timeout_seconds,
    )
    result_path = payload.get("result_path")
    if not result_path:
        raise SpawnError("paper-analyst did not return a result_path")
    return payload


def get_s2_reference_titles(db: PaperDB, arxiv_id: str) -> list[str]:
    """Placeholder: load S2 reference titles from DB once stored there."""
    neighbors = db.get_neighbors(arxiv_id, EDGE_CITES, direction="out")
    titles = []
    for neighbor in neighbors:
        paper = db.get_paper(neighbor["neighbor_id"])
        if paper and paper.get("title"):
            titles.append(paper["title"])
    return titles


def verify_analysis_result(result: dict[str, Any], s2_refs: list[str]) -> dict[str, Any]:
    """Post-verify core_cite titles against S2 references."""
    verified = dict(result)
    core_cite = verified.get("core_cite", [])
    matched_titles = []
    similarities = []
    normalized_refs = {_normalize_title(title): title for title in s2_refs if title}

    for item in core_cite:
        title = item.get("title", "")
        norm_title = _normalize_title(title)
        matched = normalized_refs.get(norm_title)
        if matched:
            matched_titles.append(title)
            similarities.append({"title": title, "score": 1.0, "matched_title": matched})

    verified["_verified"] = bool(s2_refs)
    verified["_matched"] = matched_titles
    verified["_similarities"] = similarities
    return verified


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", title.lower())).strip()


def _persist_result_copy(result: dict[str, Any], arxiv_id: str) -> str:
    DEFAULT_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_path = DEFAULT_RESULTS_DIR / f"{arxiv_id}_{timestamp}.json"
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(result_path)


def analyse_paper(
    arxiv_id: str,
    title: str,
    *,
    db: PaperDB | None = None,
    config_path: str | Path | None = None,
    spawn_executor: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Production entrypoint for analyzing one paper with retry and DB tracking."""
    llm_cfg = _load_runtime_config(config_path)
    if not llm_cfg.get("enabled", True):
        raise AnalysisError("llm_analyse is disabled in config")

    paper_db = db or PaperDB()
    _ensure_paper_stub(paper_db, arxiv_id, title)

    existing = paper_db.get_analysis_status(arxiv_id)
    if existing and existing.get("analysis_status") == "completed" and existing.get("analysis_result_path"):
        logger.info("Analysis already completed for %s, reusing cached result", arxiv_id)
        cached = safe_load_json(existing["analysis_result_path"])
        return cached

    max_retries = int(llm_cfg.get("max_retries", 1))
    models = [llm_cfg["default_model"]]
    fallback_model = llm_cfg.get("fallback_model")
    if fallback_model and fallback_model not in models:
        models.append(fallback_model)

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        model = models[min(attempt, len(models) - 1)]
        analysis_date = datetime.now().isoformat(timespec="seconds")
        paper_db.update_analysis_status(
            arxiv_id,
            analysis_status="analyzing",
            analysis_date=analysis_date,
            analysis_model=model,
        )
        try:
            logger.info("Starting analysis for %s with model=%s attempt=%s", arxiv_id, model, attempt + 1)
            spawn_result = spawn_analyst(
                arxiv_id=arxiv_id,
                title=title,
                model=model,
                timeout_seconds=int(llm_cfg.get("timeout_seconds", 300)),
                prompt_template=llm_cfg["prompt_template"],
                spawn_executor=spawn_executor,
            )
            raw_result = safe_load_json(spawn_result["result_path"])
            raw_result.setdefault("arxiv_id", arxiv_id)
            raw_result.setdefault("title", title)
            raw_result["analysis_model"] = model
            raw_result["analysis_session_id"] = spawn_result.get("session_id")
            raw_result["analysis_transcript"] = spawn_result.get("transcript_path")

            s2_refs = get_s2_reference_titles(paper_db, arxiv_id)
            verified_result = verify_analysis_result(raw_result, s2_refs)

            persisted_result_path = _persist_result_copy(verified_result, arxiv_id)
            paper_db.update_analysis_status(
                arxiv_id,
                analysis_status="completed",
                analysis_date=datetime.now().isoformat(timespec="seconds"),
                analysis_model=model,
                analysis_session_id=spawn_result.get("session_id"),
                analysis_transcript=spawn_result.get("transcript_path"),
                analysis_result_path=persisted_result_path,
            )
            logger.info("Completed analysis for %s", arxiv_id)
            return verified_result
        except Exception as exc:
            last_error = exc
            logger.exception("Analysis attempt failed for %s on attempt %s", arxiv_id, attempt + 1)
            paper_db.update_analysis_status(
                arxiv_id,
                analysis_status="failed",
                analysis_date=datetime.now().isoformat(timespec="seconds"),
                analysis_model=model,
            )

    raise AnalysisError(f"Analysis failed for {arxiv_id} after {max_retries + 1} attempts") from last_error


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sample_output = {
        "cn_oneliner": "基于已有 tokenizer 引入结构约束提升重建质量",
        "cn_abstract": "这是一段用于本地自测的占位摘要。",
        "contribution_type": "incremental",
        "editorial_note": "[前驱] 基于已有工作。[贡献] 加了一层约束。[判断] 增益可验证。",
        "why_read": "适合关注 tokenizer 设计的人快速对照实现改动。",
        "method_variants": [{"base_method": "titok", "variant_tag": "titok:self-test", "description": "本地自测占位项"}],
        "core_cite": [{"title": "TiTok: An Image Tokenizer", "role": "extends", "note": "自测用引用"}],
    }
    sample_path = DEFAULT_RESULTS_DIR / "self_test_result.json"
    DEFAULT_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    sample_path.write_text(json.dumps(sample_output, indent=2, ensure_ascii=False), encoding="utf-8")

    os.environ["ARXIV_RADAR_ANALYST_RESULT_PATH"] = str(sample_path)
    os.environ["ARXIV_RADAR_ANALYST_SESSION_ID"] = "self-test-session"
    os.environ["ARXIV_RADAR_ANALYST_TRANSCRIPT"] = str(DEFAULT_RESULTS_DIR / "self_test_transcript.json")

    result = analyse_paper("0000.00000", "Self Test Paper")
    print(json.dumps({"arxiv_id": result["arxiv_id"], "_verified": result["_verified"]}, ensure_ascii=False, indent=2))
