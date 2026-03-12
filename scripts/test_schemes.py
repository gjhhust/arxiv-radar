"""
test_schemes.py — A/B test runner for arxiv-radar v3.0 LLM schema experiments.

Runs Scheme A (Specialist Pipeline) and Scheme B (Unified Analyst) across
10 test papers × 7 wq models, writes per-paper JSON results, and generates
evaluation metrics + summary report.

Usage:
  python3 scripts/test_schemes.py --smoke              # 2 papers × 2 models × 2 schemes
  python3 scripts/test_schemes.py --all                # full 10×7×2 = 140 calls
  python3 scripts/test_schemes.py --scheme A --models wq/claude46,wq/glm5
  python3 scripts/test_schemes.py --report             # generate report only
"""

from __future__ import annotations
import argparse
import json
import logging
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

# ─────────────────── Paths ───────────────────

SCRIPT_DIR   = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
RESULTS_DIR  = PROJECT_ROOT / "data" / "test_results"

sys.path.insert(0, str(SCRIPT_DIR))

# ─────────────────── Test matrix ───────────────────

TEST_PAPERS = [
    "2603.06449", "2603.06577", "2603.07192", "2603.07057", "2603.06985",
    "2603.05800", "2603.06932", "2603.05438", "2603.08064", "2603.09086",
]

WQ_MODELS = [
    "wq/claude46",
    "wq/claude45",
    "wq/glm5",
    "wq/katcoder",
    "wq/kimik25",
    "wq/minimaxm21",
    "wq/minimaxm25",
]

SMOKE_PAPERS = TEST_PAPERS[:2]
SMOKE_MODELS = ["wq/claude46", "wq/glm5"]

VALID_CONTRIBUTION_TYPES = {"incremental", "significant", "story-heavy", "foundational"}
VALID_STANCES            = {"extends", "contrasts", "uses", "supports", "mentions"}

EXPECTED_FIELDS = [
    "cn_oneliner", "cn_abstract", "contribution_type",
    "editorial_note", "why_read", "method_variants", "key_refs",
]

logger = logging.getLogger(__name__)


# ─────────────────── DB helpers ───────────────────

def _load_paper_and_refs(paper_id: str):
    """Load paper and its outbound CITES edges from PaperDB."""
    from paper_db import PaperDB
    db = PaperDB()
    paper = db.get_paper(paper_id)
    if paper is None:
        logger.warning(f"Paper {paper_id} not found in DB")
        return None, []
    ref_edges = db.get_neighbors(paper_id, edge_type="CITES", direction="out")
    refs = [db.get_paper(e["neighbor_id"]) for e in ref_edges[:20]]
    refs = [r for r in refs if r is not None]
    return paper, refs


# ─────────────────── Run a single test cell ───────────────────

def run_cell(paper_id: str, model: str, scheme: str, force: bool = False) -> dict:
    """
    Run one test cell (paper × model × scheme).

    Returns the result dict (also written to disk).
    """
    model_short = model.split("/")[-1]
    scheme_dir  = RESULTS_DIR / f"scheme_{scheme.lower()}" / model_short
    scheme_dir.mkdir(parents=True, exist_ok=True)
    out_path = scheme_dir / f"{paper_id}.json"

    if out_path.exists() and not force:
        logger.debug(f"Skip (exists): {out_path}")
        return json.loads(out_path.read_text())

    paper, refs = _load_paper_and_refs(paper_id)
    if paper is None:
        result = {
            "paper_id":   paper_id,
            "scheme":     scheme.upper(),
            "model":      model,
            "latency_s":  0,
            "success":    False,
            "output":     {},
            "parse_error": "Paper not found in DB",
        }
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return result

    t0 = time.time()
    output: dict[str, Any] = {}
    parse_error = None
    success = False

    try:
        if scheme.upper() == "A":
            from scheme_a import analyze_paper_scheme_a
            output = analyze_paper_scheme_a(paper, refs, model=model)
        else:
            from paper_analyst import analyze_paper_scheme_b
            output = analyze_paper_scheme_b(paper, refs, model=model)

        errs = output.get("parse_errors", [])
        parse_error = "; ".join(errs) if errs else None
        success     = parse_error is None or not any("error" in e for e in errs)
    except Exception as e:
        parse_error = str(e)
        logger.error(f"Cell error [{paper_id} × {model_short} scheme_{scheme.lower()}]: {e}")

    latency = output.get("latency_s", round(time.time() - t0, 3))

    result = {
        "paper_id":   paper_id,
        "scheme":     scheme.upper(),
        "model":      model,
        "latency_s":  latency,
        "success":    success,
        "output":     {k: v for k, v in output.items()
                       if k not in ("latency_s", "parse_errors")},
        "parse_error": parse_error,
    }

    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    time.sleep(2)  # rate-limit guard between LLM calls
    return result


# ─────────────────── Test runner ───────────────────

def run_tests(papers: list[str], models: list[str], schemes: list[str],
              force: bool = False) -> list[dict]:
    """Run the full test matrix and return all result dicts."""
    cells = [
        (pid, model, scheme)
        for scheme in schemes
        for model  in models
        for pid    in papers
    ]
    total = len(cells)
    results = []

    for i, (pid, model, scheme) in enumerate(cells, 1):
        model_short = model.split("/")[-1]
        print(f"[{i}/{total}] {pid} × {model_short} (scheme_{scheme.lower()})...")
        result = run_cell(pid, model, scheme, force=force)
        results.append(result)

    return results


# ─────────────────── Report generation ───────────────────

def _load_all_results(results_dir: Path) -> list[dict]:
    """Load all per-paper JSON result files from results_dir."""
    results = []
    for scheme_dir in sorted(results_dir.iterdir()):
        if not scheme_dir.is_dir() or not scheme_dir.name.startswith("scheme_"):
            continue
        for model_dir in sorted(scheme_dir.iterdir()):
            if not model_dir.is_dir():
                continue
            for json_file in sorted(model_dir.glob("*.json")):
                try:
                    results.append(json.loads(json_file.read_text()))
                except Exception as e:
                    logger.warning(f"Failed to load {json_file}: {e}")
    return results


def _compute_metrics(results: list[dict]) -> dict:
    """
    Compute per-(scheme, model) evaluation metrics.

    Returns nested dict: metrics[scheme][model] = {metric: value}
    """
    from collections import defaultdict

    # Group results by (scheme, model)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in results:
        key = (r.get("scheme", "?").upper(), r.get("model", "?"))
        groups[key].append(r)

    metrics = {}
    for (scheme, model), cells in sorted(groups.items()):
        total = len(cells)
        if total == 0:
            continue

        # Parse success rate
        successes = sum(1 for c in cells if c.get("success", False))

        # Fill rate — fraction of expected fields that are non-empty
        fill_scores = []
        for c in cells:
            out = c.get("output", {})
            filled = sum(
                1 for f in EXPECTED_FIELDS
                if out.get(f) not in (None, "", [], {})
            )
            fill_scores.append(filled / len(EXPECTED_FIELDS))

        # Enum validity — contribution_type must be one of 4 values
        enum_valid = sum(
            1 for c in cells
            if c.get("output", {}).get("contribution_type", "") in VALID_CONTRIBUTION_TYPES
        )

        # Average latency
        latencies = [c.get("latency_s", 0) for c in cells if c.get("latency_s", 0) > 0]
        avg_latency = round(sum(latencies) / len(latencies), 2) if latencies else 0

        scheme_key = f"scheme_{scheme.lower()}"
        if scheme_key not in metrics:
            metrics[scheme_key] = {}
        metrics[scheme_key][model] = {
            "total":              total,
            "parse_success_rate": round(successes / total, 3),
            "fill_rate":          round(sum(fill_scores) / total, 3),
            "avg_latency_s":      avg_latency,
            "enum_valid_rate":    round(enum_valid / total, 3),
        }

    return metrics


def _best_model(scheme_metrics: dict, metric: str = "parse_success_rate") -> str | None:
    """Return the model with the highest value for a given metric."""
    if not scheme_metrics:
        return None
    return max(scheme_metrics, key=lambda m: scheme_metrics[m].get(metric, 0))


def _first_success(results: list[dict], scheme: str, model: str) -> dict | None:
    """Return the first successful result for a given scheme + model."""
    for r in results:
        if r.get("scheme", "").upper() == scheme.upper() and r.get("model") == model:
            if r.get("success"):
                return r
    return None


def _markdown_table(headers: list[str], rows: list[list]) -> str:
    """Render a simple Markdown table."""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))

    def fmt_row(cells):
        return "| " + " | ".join(str(c).ljust(col_widths[i]) for i, c in enumerate(cells)) + " |"

    sep = "| " + " | ".join("-" * w for w in col_widths) + " |"
    lines = [fmt_row(headers), sep] + [fmt_row(row) for row in rows]
    return "\n".join(lines)


def generate_report(results_dir: Path | None = None) -> None:
    """
    Read all result JSONs, compute metrics, write evaluation.json + summary.md.

    Args:
        results_dir: path to test results root; defaults to RESULTS_DIR
    """
    results_dir = results_dir or RESULTS_DIR
    if not results_dir.exists():
        print(f"Results dir not found: {results_dir}")
        return

    results = _load_all_results(results_dir)
    if not results:
        print("No result files found.")
        return

    print(f"Loaded {len(results)} result files from {results_dir}")

    metrics = _compute_metrics(results)

    # Write evaluation.json
    eval_path = results_dir / "evaluation.json"
    eval_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False))
    print(f"Written: {eval_path}")

    # ── Build summary.md ──────────────────────────────────────────────────
    today    = date.today().isoformat()
    lines    = [f"# A/B Test Summary — {today}", ""]

    schemes  = sorted(metrics.keys())  # scheme_a, scheme_b

    # Helper: build rows for a given metric key
    def metric_rows(metric_key: str) -> tuple[list[str], list[list]]:
        all_models = sorted({m for s in schemes for m in metrics.get(s, {})})
        headers = ["Model"] + [s.replace("_", " ").title() for s in schemes]
        rows = []
        for model in all_models:
            row = [model]
            for s in schemes:
                val = metrics.get(s, {}).get(model, {}).get(metric_key, "—")
                row.append(val)
            rows.append(row)
        return headers, rows

    # Table 1: parse_success_rate
    lines += ["## Parse Success Rate", ""]
    h, r = metric_rows("parse_success_rate")
    lines += [_markdown_table(h, r), ""]

    # Table 2: fill_rate
    lines += ["## Fill Rate", ""]
    h, r = metric_rows("fill_rate")
    lines += [_markdown_table(h, r), ""]

    # Table 3: avg_latency_s
    lines += ["## Average Latency (s)", ""]
    h, r = metric_rows("avg_latency_s")
    lines += [_markdown_table(h, r), ""]

    # Table 4: enum_valid_rate
    lines += ["## Contribution-Type Enum Valid Rate", ""]
    h, r = metric_rows("enum_valid_rate")
    lines += [_markdown_table(h, r), ""]

    # ── Sample Output ─────────────────────────────────────────────────────
    lines += ["## Sample Output", ""]
    for s in schemes:
        if s not in metrics:
            continue
        best = _best_model(metrics[s])
        if not best:
            continue
        scheme_letter = s.split("_")[-1].upper()
        sample = _first_success(results, scheme_letter, best)
        if not sample:
            continue
        out = sample.get("output", {})
        lines += [
            f"### {s.replace('_', ' ').title()} — best model: `{best}`",
            "",
            f"**Paper:** `{sample['paper_id']}`  |  "
            f"**Latency:** {sample.get('latency_s', '?')}s",
            "",
            f"- **cn_oneliner:** {out.get('cn_oneliner', '—')}",
            f"- **contribution_type:** `{out.get('contribution_type', '—')}`",
            f"- **editorial_note:** {out.get('editorial_note', '—')}",
            f"- **why_read:** {out.get('why_read', '—')}",
        ]
        mv = out.get("method_variants", [])
        if mv:
            lines.append("- **method_variants:**")
            for v in mv[:3]:
                lines.append(f"  - `{v.get('variant_tag', '?')}` — {v.get('description', '')}")
        lines.append("")

    summary_path = results_dir / "summary.md"
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Written: {summary_path}")


# ─────────────────── CLI ───────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="arxiv-radar v3 A/B test runner",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--all",    action="store_true", help="Full test: 10 papers × 7 models × 2 schemes")
    mode.add_argument("--smoke",  action="store_true", help="Quick smoke: 2 papers × 2 models × 2 schemes")
    mode.add_argument("--report", action="store_true", help="Generate report only (no new LLM calls)")

    parser.add_argument("--scheme", choices=["A", "B"], help="Run only scheme A or B")
    parser.add_argument("--models", help="Comma-separated model list, e.g. wq/claude46,wq/glm5")
    parser.add_argument("--force",  action="store_true", help="Re-run even if output file exists")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")

    if args.report:
        generate_report()
        return

    # Determine which papers / models / schemes to run
    if args.smoke:
        papers  = SMOKE_PAPERS
        models  = SMOKE_MODELS
        schemes = ["A", "B"]
    elif args.all:
        papers  = TEST_PAPERS
        models  = WQ_MODELS
        schemes = ["A", "B"]
    else:
        papers  = TEST_PAPERS
        models  = WQ_MODELS
        schemes = ["A", "B"]

    if args.scheme:
        schemes = [args.scheme.upper()]
    if args.models:
        models = [m.strip() for m in args.models.split(",") if m.strip()]

    print(f"Test matrix: {len(papers)} papers × {len(models)} models × {len(schemes)} schemes "
          f"= {len(papers) * len(models) * len(schemes)} calls")

    run_tests(papers, models, schemes, force=args.force)

    print("\nGenerating report...")
    generate_report()


if __name__ == "__main__":
    main()
