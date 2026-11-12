import json
from typing import List, Optional
from pydantic import BaseModel, Field
from src.agents.llm_cache import invoke_gemini_structured
from src.agents.catalog import DatasetCatalog

# -------------------------------------------------------------------------
# 1. Structured Output Schemas (Pydantic v2)
# -------------------------------------------------------------------------

class ColumnCleaningRule(BaseModel):
    column_name: str = Field(
        description="Exact name of the column as defined in the catalog."
    )
    target_type: str = Field(
        description="Target PostgreSQL type to cast to (e.g. TIMESTAMPTZ, NUMERIC(10,2), VARCHAR(50))."
    )
    anomalies_detected: List[str] = Field(
        description="Specific dirty data patterns and catalog violations observed in this column."
    )
    business_risk: str = Field(
        description="Detailed operational risk of leaving this column dirty (e.g. financial audit distortion, SLA misreporting)."
    )
    severity: str = Field(
        description="Severity assessment: 'CRITICAL', 'WARNING', or 'INFO'."
    )
    sql_transformation: str = Field(
        description=(
            "Idempotent PostgreSQL CASE or transformation expression that resolves all anomalies "
            "and casts to the target_type. Example: CASE WHEN ... THEN ... ELSE NULL END"
        )
    )


class TableIntegrityRule(BaseModel):
    rule_name: str = Field(
        description="Short identifier for the multi-column or table-level rule."
    )
    description: str = Field(
        description="Explanation of why this cross-column relationship must hold."
    )
    sql_constraint_or_filter: str = Field(
        description="PostgreSQL WHERE condition or boolean expression used to filter or flag invalid rows."
    )


class ComprehensiveAuditReport(BaseModel):
    executive_summary: str = Field(
        description="High-level assessment of dataset hygiene, operational reliability, and major pipeline risks."
    )
    column_rules: List[ColumnCleaningRule] = Field(
        description="Actionable cleaning and casting rules for EVERY column in the catalog."
    )
    table_integrity_rules: List[TableIntegrityRule] = Field(
        description="Cross-column table constraints (e.g., temporal inversions, lifecycle state vs. timestamp checks)."
    )


# -------------------------------------------------------------------------
# 2. Agent Execution Function
# -------------------------------------------------------------------------

def run_generalized_dq_agent(
    catalog: DatasetCatalog, 
    empirical_profile: dict
) -> ComprehensiveAuditReport:
    """
    Generalized Data Quality Agent:
    Reconciles expected business rules from the DatasetCatalog against empirical
    anomalies discovered by the catalog-driven profiler, producing an audit report
    and production-ready PostgreSQL transformation rules.
    """
    print("[DQ Agent] Preparing structured audit request (cache-first)...")

    system_prompt = """
You are a Principal Data Quality Architect and PostgreSQL 16 Pipeline Specialist.
You audit raw bronze operational datasets against an official business DatasetCatalog and synthesize production-grade SQL transformation expressions for the Silver layer.

You will be given:
1. `catalog_specification`: Business definitions, expected data types, formatting regexes, known sentinels, and cross-column constraints.
2. `empirical_table_profile`: Database profiling statistics, null counts, distinct cardinality, length distributions, and specific violation counts from empirical checks.

Your task:
- Audit EVERY column defined in the catalog against its expected data contract.
- Formulate explicit, idempotent PostgreSQL 16 SQL expressions (e.g., CASE statements) for each column in `column_rules`.
- Formulate table-level and cross-column integrity rules in `table_integrity_rules`.

STRICT TECHNICAL & POSTGRESQL 16 EXECUTION CONSTRAINTS:

1. POSTGRESQL 16 DIALECT ONLY:
   - NEVER generate `TRY_CAST`. PostgreSQL does not support `TRY_CAST`. Any invocation of `TRY_CAST` will cause a fatal database error.
   - Guard all type casts (`::TIMESTAMP`, `::NUMERIC`, `::BIGINT`) using preceding `CASE WHEN <regex_pattern> THEN ...` checks to prevent runtime syntax crashes.
   - Valid POSIX regex syntax only: NEVER start a regex with a quantifier (e.g., never write `*` or `+` at the start of a pattern like `*\$?`). To match optional dollar signs, write `^\$?-?[0-9]+(\.[0-9]+)?$`.

2. IDEMPOTENCY & STABLE IDENTIFIERS:
   - NEVER use non-deterministic functions like `GEN_RANDOM_UUID()` or `RANDOM()`. Transformations must be 100% reproducible on re-execution.
   - When generating synthetic IDs for missing or invalid `ticket_id` values, derive them deterministically from the raw row hash:
     `'TKT-SYNTH-' || SUBSTRING(_row_hash, 1, 8)`

3. AGENT BOUNDARY FOR FREE-TEXT COLUMNS (`category` and `description`):
   - DO NOT truncate, swap, or nullify `category` or `description` here, and DO NOT replace them with generic tags like 'Uncategorized' or 'General Maintenance Issue'.
   - Column swapping, entity extraction, and canonical category mapping are handled downstream by a separate, dedicated Semantic Classification Agent (Agent 2).
   - In this Data Quality Agent, preserve all raw text cleanly by generating:
     * For `category`: `TRIM(raw.category)::TEXT` (or `COALESCE(TRIM(raw.category), 'Uncategorized')::TEXT` if null).
     * For `description`: `TRIM(raw.description)::TEXT` (or `COALESCE(TRIM(raw.description), 'No description provided')::TEXT` if null).

4. ZERO DATA LOSS ROBUST TIMESTAMP PARSING (`created_at`, `resolved_at`):
   Target timestamp format must be standard `YYYY-MM-DD HH24:MI:SS`. If a record has only date information (day, month, year) without time, preserve the date and set time to `00:00:00`.
   You MUST include explicit branches in your CASE statement for ALL date patterns found in the empirical profile in priority order:
   * Known text sentinels ('00/00/0000', 'asap', 'not a date', 'TBD', 'never', 'pending', '???'): Map to `NULL`.
   * 10-digit Unix epoch timestamps (`^[0-9]{10}$`): Convert via `TO_TIMESTAMP(TRIM(col)::BIGINT)`.
   * ISO-8601 timestamps (`^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}`): Convert via `REPLACE(TRIM(col), 'T', ' ')::TIMESTAMP`.
   * ISO Date only (`^[0-9]{4}-[0-9]{2}-[0-9]{2}$`): Convert via `TO_DATE(TRIM(col), 'YYYY-MM-DD')::TIMESTAMP` (defaults time to 00:00:00).
   * US Slash dates with 12-hr AM/PM (`^[0-9]{2}/[0-9]{2}/[0-9]{4} [0-9]{2}:[0-9]{2} (AM|PM)$`): Convert via `TO_TIMESTAMP(TRIM(col), 'MM/DD/YYYY HH12:MI AM')`.
   * US Slash Date only (`^[0-9]{2}/[0-9]{2}/[0-9]{4}$`): Convert via `TO_DATE(TRIM(col), 'MM/DD/YYYY')::TIMESTAMP` (defaults time to 00:00:00).
   * Alpha Month dates (`^[0-9]{2}-[A-Za-z]{3}-[0-9]{4} [0-9]{2}:[0-9]{2}$`): Convert via `TO_TIMESTAMP(TRIM(col), 'DD-Mon-YYYY HH24:MI')`.
   * Hyphenated MDY dates (`^[0-9]{2}-[0-9]{2}-[0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2}$`): Convert via `TO_TIMESTAMP(TRIM(col), 'MM-DD-YYYY HH24:MI:SS')`.
   * Fallback for unrecognized formats: Return `NULL`.
   Cast the final result to `::TIMESTAMP`.

5. NUMERIC & MONETARY SANITIZATION:
   - `cost`: Strip currency symbols (`$`) and commas. If cost is in ('TBD', 'error', '-1', '-999') or is negative (< 0), return `NULL`. Otherwise cast valid numbers to `NUMERIC(10,2)`.
   - `sla_hours`: If sla_hours is in ('999', '999.0', '-1', '-1.0', '0', '0.0') or is non-positive (<= 0), return `NULL`. Otherwise cast valid numbers to `NUMERIC(6,1)`.

6. CANONICAL ENUM & VALUE STANDARDIZATION:
   - `priority`: Standardize variations to 4 canonical tiers: `'CRITICAL'`, `'HIGH'`, `'MEDIUM'`, `'LOW'`.
     * 'crit', 'critical', 'urgent!!!', 'asap' -> `'CRITICAL'`
     * 'hi', 'high' -> `'HIGH'`
     * 'med', 'medium', 'normal' -> `'MEDIUM'` (Map 'normal' explicitly to 'MEDIUM')
     * 'lo', 'low' -> `'LOW'`
     * Missing, NULL, or '???' -> Default to `'MEDIUM'`
   - `status`: Standardize lifecycle values to Title Case (`'Open'`, `'In Progress'`, `'Pending Vendor'`, `'Escalated'`, `'Resolved'`, `'Closed'`). Map corrupted sentinels ('???', 'unknown') or NULL to `'Unassigned'`.
   - `building`: Map test artifacts ('???', 'unknown', 'test building') or NULLs to `NULL`.

7. CROSS-COLUMN INTEGRITY & TEMPORAL VALIDATION:
   - For `table_integrity_rules`, define validation logic for temporal anomalies where `resolved_at < created_at`.
   - Document how inverted timestamps are handled: flag them via `is_temporal_anomaly = TRUE` and set `resolved_at = NULL` in Silver to avoid corrupting duration/SLA metrics.
"""

    payload = {
        "catalog_specification": catalog.model_dump(),
        "empirical_table_profile": empirical_profile
    }

    user_prompt = (
        "Here is the Dataset Catalog alongside the empirical profile from the Bronze layer. "
        "Analyze the discrepancies and generate the comprehensive audit report and SQL transformation rules:\n\n"
        f"{json.dumps(payload, indent=2, default=str)}"
    )

    print("[DQ Agent] Running LLM reasoning over catalog specification and empirical profile...")
    messages = [
        ("system", system_prompt),
        ("user", user_prompt)
    ]
    report: ComprehensiveAuditReport = invoke_gemini_structured(
        ComprehensiveAuditReport, messages, temperature=0.1
    )

    return report
