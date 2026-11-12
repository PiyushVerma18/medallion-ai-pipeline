"""Evaluate agent outputs against a small, labeled regression set.

This is deliberately separate from tests/test_pipeline_invariants.py: it calls
the production agent prompts and scores their responses against expected labels.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agents.catalog import TICKETS_CATALOG
from src.agents.data_quality import run_generalized_dq_agent
from src.agents.llm_cache import get_llm_call_stats, reset_llm_call_stats
from src.agents.semantic_clean import (
    CANONICAL_TAXONOMY,
    classify_categories,
    resolve_field_swap_candidates,
)

FIXTURE = Path(__file__).with_name("agent_eval_cases.json")


def _normalized_text(value: str) -> str:
    return " ".join((value or "").split())


def _score_taxonomy(cases: list, results: list) -> Dict[str, Any]:
    predictions = {item.raw_category: item for item in results}
    errors = []
    for case in cases:
        prediction = predictions.get(case["raw_category"])
        actual = prediction.canonical_category if prediction else None
        if actual != case["expected"] or actual not in CANONICAL_TAXONOMY:
            errors.append({"case": case["raw_category"], "expected": case["expected"], "actual": actual})
    return {
        "cases": len(cases),
        "correct": len(cases) - len(errors),
        "accuracy": (len(cases) - len(errors)) / len(cases) if cases else 1.0,
        "errors": errors,
    }


def _score_swaps(cases: list, results: list) -> Dict[str, Any]:
    predictions = {item.ticket_id: item for item in results}
    errors = []
    for case in cases:
        prediction = predictions.get(case["ticket_id"])
        actual = None if prediction is None else {
            "is_swapped": prediction.is_swapped,
            "category": _normalized_text(prediction.clean_category),
            "description": _normalized_text(prediction.clean_description),
        }
        expected = {
            "is_swapped": case["expected_swapped"],
            "category": _normalized_text(case["expected_category"]),
            "description": _normalized_text(case["expected_description"]),
        }
        if actual != expected:
            errors.append({"case": case["ticket_id"], "expected": expected, "actual": actual})
    return {
        "cases": len(cases),
        "correct": len(cases) - len(errors),
        "accuracy": (len(cases) - len(errors)) / len(cases) if cases else 1.0,
        "errors": errors,
    }


def _score_dq(report: Any, catalog: Any) -> Dict[str, Any]:
    expected = {column.name: column.target_type for column in catalog.columns}
    actual = [rule.column_name for rule in report.column_rules]
    actual_set = set(actual)
    issues = []
    if actual_set != set(expected):
        issues.append({
            "missing_columns": sorted(set(expected) - actual_set),
            "unexpected_columns": sorted(actual_set - set(expected)),
        })
    if len(actual) != len(actual_set):
        issues.append({"duplicate_columns": sorted(name for name in actual_set if actual.count(name) > 1)})
    for rule in report.column_rules:
        if rule.column_name in expected and rule.target_type.strip().upper() != expected[rule.column_name].strip().upper():
            issues.append({"column": rule.column_name, "issue": "target_type_mismatch", "expected": expected[rule.column_name], "actual": rule.target_type})
        if not rule.sql_transformation.strip():
            issues.append({"column": rule.column_name, "issue": "empty_sql_transformation"})
        if rule.severity.upper() not in {"CRITICAL", "WARNING", "INFO"}:
            issues.append({"column": rule.column_name, "issue": "invalid_severity", "actual": rule.severity})
        forbidden = [token for token in ("TRY_CAST", "RANDOM()", "GEN_RANDOM_UUID()") if token in rule.sql_transformation.upper()]
        if forbidden:
            issues.append({"column": rule.column_name, "issue": "forbidden_sql", "tokens": forbidden})
    if not report.executive_summary.strip():
        issues.append({"issue": "empty_executive_summary"})
    if not report.table_integrity_rules:
        issues.append({"issue": "missing_table_integrity_rules"})
    return {
        "expected_columns": len(expected),
        "returned_columns": len(actual),
        "format_compliant": not issues,
        "issues": issues,
    }


def _thresholds(args: argparse.Namespace) -> Dict[str, float]:
    return {"taxonomy": args.taxonomy_min, "swaps": args.swap_min, "dq_format": args.dq_format_min, "overall": args.overall_min}


def _comparable_scores(report: dict) -> Dict[str, float]:
    scores = report.get("scores", {})
    taxonomy = scores.get("taxonomy_subscore", scores.get("taxonomy"))
    swaps = scores.get("field_swap_subscore", scores.get("swaps"))
    semantic = scores.get("semantic_classification")
    if semantic is None and taxonomy is not None and swaps is not None:
        semantic = (taxonomy + swaps) / 2
    dq = scores.get("data_quality", scores.get("dq_format"))
    result = {}
    for key, value in (
        ("semantic_classification", semantic),
        ("taxonomy_subscore", taxonomy),
        ("field_swap_subscore", swaps),
        ("data_quality", dq),
        ("overall", scores.get("overall")),
    ):
        if isinstance(value, (int, float)):
            result[key] = float(value)
    return result


def _token_price(env_key: str) -> Any:
    raw = os.getenv(env_key, "").strip()
    return float(raw) if raw else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--taxonomy-min", type=float, default=0.85)
    parser.add_argument("--swap-min", type=float, default=0.85)
    parser.add_argument("--dq-format-min", type=float, default=1.0)
    parser.add_argument("--overall-min", type=float, default=0.90)
    parser.add_argument(
        "--baseline-report",
        type=Path,
        help="Optional prior JSON report; fail if an agent or overall score regresses too far",
    )
    parser.add_argument(
        "--max-score-drop",
        type=float,
        default=0.05,
        help="Maximum allowed score decrease against --baseline-report (default: 0.05)",
    )
    parser.add_argument("--output", type=Path, help="Optional path for the JSON evaluation report")
    args = parser.parse_args()
    if not 0 <= args.max_score_drop <= 1:
        parser.error("--max-score-drop must be between 0 and 1")

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    reset_llm_call_stats()
    timings: Dict[str, float] = {}

    start = time.perf_counter()
    taxonomy_results = classify_categories([case["raw_category"] for case in fixture["taxonomy_cases"]])
    timings["semantic_taxonomy_seconds"] = time.perf_counter() - start
    taxonomy = _score_taxonomy(fixture["taxonomy_cases"], taxonomy_results)

    start = time.perf_counter()
    swap_results = resolve_field_swap_candidates([
        {key: case[key] for key in ("ticket_id", "raw_category", "raw_description")}
        for case in fixture["swap_cases"]
    ])
    timings["semantic_swap_seconds"] = time.perf_counter() - start
    swaps = _score_swaps(fixture["swap_cases"], swap_results)

    start = time.perf_counter()
    dq_report = run_generalized_dq_agent(TICKETS_CATALOG, fixture["dq_profile"])
    timings["data_quality_seconds"] = time.perf_counter() - start
    dq = _score_dq(dq_report, TICKETS_CATALOG)

    # The project has two agents. The semantic agent score combines its two
    # tasks equally; the overall score then weights the two agents equally.
    dq_score = 1.0 if dq["format_compliant"] else 0.0
    semantic_score = (taxonomy["accuracy"] + swaps["accuracy"]) / 2
    overall = (semantic_score + dq_score) / 2
    thresholds = _thresholds(args)
    scores = {
        "semantic_classification": semantic_score,
        "taxonomy_subscore": taxonomy["accuracy"],
        "field_swap_subscore": swaps["accuracy"],
        "data_quality": dq_score,
        "overall": overall,
    }
    quality_gates_passed = (
        taxonomy["accuracy"] >= thresholds["taxonomy"]
        and swaps["accuracy"] >= thresholds["swaps"]
        and dq_score >= thresholds["dq_format"]
        and overall >= thresholds["overall"]
    )
    comparison = None
    regression_gate_passed = True
    if args.baseline_report:
        baseline = json.loads(args.baseline_report.read_text(encoding="utf-8"))
        baseline_scores = _comparable_scores(baseline)
        current_scores = _comparable_scores({"scores": scores})
        comparisons = {}
        for name, baseline_score in baseline_scores.items():
            if name not in current_scores:
                continue
            delta = current_scores[name] - baseline_score
            comparisons[name] = {
                "baseline": baseline_score,
                "current": current_scores[name],
                "delta": delta,
                "max_allowed_drop": args.max_score_drop,
                "passed": delta >= -args.max_score_drop,
            }
        regression_gate_passed = all(item["passed"] for item in comparisons.values()) and bool(comparisons)
        comparison = {
            "baseline_report": str(args.baseline_report),
            "comparisons": comparisons,
            "passed": regression_gate_passed,
        }

    passed = quality_gates_passed and regression_gate_passed
    call_stats = get_llm_call_stats()
    estimated_input_tokens = round(call_stats["provider_input_chars"] / 4)
    estimated_output_tokens = round(call_stats["provider_output_chars"] / 4)
    input_rate = _token_price("GEMINI_INPUT_USD_PER_1M_TOKENS")
    output_rate = _token_price("GEMINI_OUTPUT_USD_PER_1M_TOKENS")
    estimated_cost = None
    if input_rate is not None and output_rate is not None:
        estimated_cost = round(
            estimated_input_tokens * input_rate / 1_000_000
            + estimated_output_tokens * output_rate / 1_000_000,
            8,
        )
    report = {
        "evaluation_set": str(FIXTURE.relative_to(ROOT)),
        "passed": passed,
        "scores": scores,
        "thresholds": thresholds,
        "quality_gates_passed": quality_gates_passed,
        "baseline_comparison": comparison,
        "agents": {
            "semantic_classification": {
                "score": semantic_score,
                "taxonomy_normalization": taxonomy,
                "field_swap_detection": swaps,
            },
            "data_quality": dq,
        },
        "latency_seconds": timings,
        "llm_calls": call_stats,
        "cost_estimate": {
            "estimated_input_tokens": estimated_input_tokens,
            "estimated_output_tokens": estimated_output_tokens,
            "estimated_cost_usd": estimated_cost,
            "note": (
                "Token counts are rough character-based estimates. Cost uses GEMINI_INPUT_USD_PER_1M_TOKENS and "
                "GEMINI_OUTPUT_USD_PER_1M_TOKENS when both are set; otherwise cost is null."
            ),
        },
    }
    serialized = json.dumps(report, indent=2, ensure_ascii=False)
    print(serialized)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
