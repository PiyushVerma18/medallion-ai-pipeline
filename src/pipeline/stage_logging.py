"""Small shared logging helpers for pipeline stage boundaries."""

import functools
import json
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


_run_id = "not-started"
_LOG = logging.getLogger("medallion.pipeline")


class _RunIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id
        return True


def configure_pipeline_logging() -> str:
    """Configure console logging and a fresh log file for this pipeline run."""
    global _run_id
    _run_id = uuid.uuid4().hex[:12]

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # If another run starts in this process, close its handlers so events
    # cannot continue to append to the previous run's file.
    for handler in list(root.handlers):
        if getattr(handler, "_medallion_handler", False):
            root.removeHandler(handler)
            handler.close()

    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    log_path = log_dir / f"pipeline_{timestamp}_{_run_id}.log"
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s run_id=%(run_id)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    run_filter = _RunIdFilter()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(run_filter)
    console._medallion_handler = True

    file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(run_filter)
    file_handler._medallion_handler = True

    root.addHandler(console)
    root.addHandler(file_handler)
    _LOG.info("pipeline_run_started log_file=%s", log_path)
    return _run_id


def _result_summary(result: Any) -> dict:
    """Extract small operational counters without logging full agent payloads."""
    if not isinstance(result, dict):
        return {}

    summary = {}
    scalar_keys = (
        "source_rows", "new_inserted", "total_bronze_records",
        "total_rows", "distinct_categories_count", "swapped_candidates_count",
        "extracted_resolutions_count", "pipeline_status",
    )
    for key in scalar_keys:
        value = result.get(key)
        if isinstance(value, (str, int, float, bool)):
            summary[key] = value

    profile = result.get("profile_data")
    if isinstance(profile, dict):
        for key in ("total_rows",):
            if key in profile:
                summary[f"profile_{key}"] = profile[key]
        violations = profile.get("cross_column_violations")
        if violations is not None:
            summary["cross_column_violation_count"] = len(violations)

    dq_report = result.get("dq_audit_report")
    if dq_report is not None:
        summary["dq_column_rule_count"] = len(getattr(dq_report, "column_rules", []))
        summary["dq_integrity_rule_count"] = len(getattr(dq_report, "table_integrity_rules", []))

    metrics = result.get("metrics")
    if metrics is not None:
        fields = getattr(metrics, "__dict__", {})
        for key, value in fields.items():
            if isinstance(value, (str, int, float, bool)):
                summary[key] = value

    for key in ("extracted_descriptions_stats",):
        value = result.get(key)
        if isinstance(value, dict):
            summary.update({f"{key}.{k}": v for k, v in value.items() if isinstance(v, (str, int, float, bool))})
    return summary


def log_stage(stage_name: str) -> Callable:
    """Log stage start, elapsed time, safe result counters, and failures."""
    def decorate(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            _LOG.info("stage_started stage=%s", stage_name)
            try:
                result = func(*args, **kwargs)
            except Exception:
                elapsed = time.perf_counter() - started
                _LOG.exception("stage_failed stage=%s elapsed_seconds=%.3f", stage_name, elapsed)
                raise
            elapsed = time.perf_counter() - started
            summary = json.dumps(_result_summary(result), sort_keys=True, default=str)
            _LOG.info(
                "stage_completed stage=%s elapsed_seconds=%.3f summary=%s",
                stage_name, elapsed, summary,
            )
            return result
        return wrapped
    return decorate
