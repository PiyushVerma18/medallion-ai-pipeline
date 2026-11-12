import json
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from sqlalchemy import text
from src.agents.llm_cache import invoke_gemini_structured
from src.agents.sql_safety import validate_gold_select, validate_gold_target
from src.db import engine as admin_engine, gold_engine

# =========================================================================
# 1. Pydantic Schemas
# =========================================================================

class ColumnSemanticMeaning(BaseModel):
    column_name: str
    sample_values: List[str] = Field(default_factory=list, description="Top distinct sample values from the actual database.")
    physical_real_world_meaning: str = Field(description="What this field actually represents in corporate facility operations.")
    analytical_pitfalls_or_traps: str = Field(description="Quality issues to guard against (e.g., negative duration, duplicate counting, nulls).")

class SchemaSemanticUnderstanding(BaseModel):
    executive_understanding: str
    column_mappings: List[ColumnSemanticMeaning]

class StakeholderProfile(BaseModel):
    title: str = Field(description="Role title (e.g., 'VP of Corporate Real Estate', 'Chief Reliability Engineer', 'Procurement & Vendor Contract Lead')")
    primary_mandate: str = Field(description="Their core organizational responsibility or goal.")
    current_pain_point: str = Field(description="The specific operational visibility gap or frustration they experience with raw ticket logs.")

class GoldProblemStatement(BaseModel):
    problem_title: str
    target_table_name: str = Field(
        description="PostgreSQL view/table name prefixed with 'gold.' (e.g., 'gold.annual_facilities_opex_summary'). Lowercase snake_case."
    )
    why_this_problem_matters: str = Field(
        description="The strategic 'Why Behind': Why this problem is critical, the cost of inaction or ignorance, and what business failure occurs if not monitored."
    )
    key_stakeholders: List[StakeholderProfile] = Field(
        description="Detailed profiles of the 2-3 primary stakeholders who depend on this model."
    )
    how_model_solves_problem: str = Field(
        description="A concise narrative explaining exactly how this specific Gold model's grain, dimensions, and aggregations solve the problem and eliminate raw data confusion."
    )
    decisions_enabled: List[str] = Field(
        description="Concrete, high-value business actions and operational interventions enabled by this model."
    )
    required_dimensions: List[str] = Field(description="Dimensions to group by (e.g., incident_year, building, canonical_category)")
    required_metrics: List[str] = Field(description="Metrics that must be derived to solve the problem")
    temporal_grain: str = Field(default="incident_year", description="Must be annual grain: EXTRACT(YEAR FROM created_at)::INT")

class DomainProblemsProposal(BaseModel):
    domain_name: str
    domain_thesis: str = Field(description="Overarching strategic thesis for why this domain requires dedicated Gold dimensional modeling.")
    problems: List[GoldProblemStatement] = Field(
        description="Exactly 2 distinct, complementary high-impact business problem statements to solve for this domain at the annual grain."
    )

class VerifiedSqlSolution(BaseModel):
    target_table_name: str
    self_verification_rationale: str
    raw_query_sql: str = Field(description="Clean, executable PostgreSQL SELECT query (without CREATE VIEW/TABLE wrapper).")

class StatAuditResult(BaseModel):
    is_statistically_sound: bool = Field(description="True if all statistical values are physically possible and conform to business logic.")
    detected_violations: List[str] = Field(description="List of issues (e.g., 'avg_resolution_time_hours is negative: min = -11602.22', 'cost is negative').")
    suggested_sql_patch: Optional[str] = Field(None, description="Explicit SQL clause fix needed to eliminate the issue.")


# =========================================================================
# 2. Agent Core Functions
# =========================================================================

def introspect_silver_column_context(table_name: str = "silver.cleaned_tickets", sample_limit: int = 5) -> Dict[str, Any]:
    """
    Dynamically introspects the database table to extract column data types
    and top distinct sample values. Provides empirical data grounding so the LLM
    never guesses categorical casing, spelling, or formatting.
    """
    with gold_engine.connect() as conn:
        cols_query = text("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_schema = 'silver' AND table_name = 'cleaned_tickets'
              AND column_name <> '_silver_processed_at'
            ORDER BY ordinal_position;
        """)
        cols = conn.execute(cols_query).fetchall()

        column_context = {}
        for col_name, data_type in cols:
            safe_col = f'"{col_name}"'
            sample_query = text(f"""
                SELECT DISTINCT {safe_col}::TEXT AS val 
                FROM {table_name} 
                WHERE {safe_col} IS NOT NULL 
                ORDER BY val
                LIMIT {sample_limit};
            """)
            samples = [str(r[0]) for r in conn.execute(sample_query).fetchall() if r[0] is not None]
            column_context[col_name] = {
                "data_type": data_type,
                "sample_distinct_values": samples
            }
        return column_context

def agent_understand_schema(column_context: Dict[str, Any]) -> SchemaSemanticUnderstanding:
    """Step 1: Understand real-world semantics and operational traps using empirical column profiles."""
    prompt = f"""
You are a Principal Data Warehouse Architect in Corporate Facilities Management.
Examine this empirical schema profile from our PostgreSQL table (`silver.cleaned_tickets`), 
including each column's data type and top distinct sample values:
{json.dumps(column_context, indent=2)}

For each column:
1. Retain the `sample_values` as provided.
2. Explain what it represents in physical facility operations.
3. Identify analytical pitfalls or quality traps (e.g., temporal inversions, duplicates, nulls).
"""
    return invoke_gemini_structured(SchemaSemanticUnderstanding, prompt, temperature=0.0)


def agent_formulate_domain_problems(
    schema_understanding: SchemaSemanticUnderstanding,
    domain_name: str,
    business_domain_narrative: str
) -> List[GoldProblemStatement]:
    """Step 2: Formulate 2 deeply justified problem statements with full stakeholder context."""
    prompt = f"""
You are a Principal Enterprise Data Warehouse Architect and Strategic Operations Consultant.
Analyze the Silver schema semantics:
{schema_understanding.model_dump_json(indent=2)}

Business Domain: "{domain_name}"
Context Narrative:
\"\"\"{business_domain_narrative}\"\"\"

For this domain, formulate EXACTLY 2 distinct, high-impact business problem statements at the annual grain (`incident_year`).
For EACH problem statement, provide thorough analytical depth:
1. `why_this_problem_matters`: Explain the deep operational "why behind", including the financial or business risk of not tracking this.
2. `key_stakeholders`: Provide specific profiles for 2-3 roles (their title, core mandate, and current blind spot).
3. `how_model_solves_problem`: Explain how this specific Gold model (its grain, dimensions, semantic filters, and metrics) solves that blind spot.
4. `decisions_enabled`: List concrete operational decisions facilities leadership can make.
5. `target_table_name`: Valid lowercase snake_case prefixed with 'gold.'.
"""
    proposal: DomainProblemsProposal = invoke_gemini_structured(
        DomainProblemsProposal, prompt, temperature=0.0
    )
    return proposal.problems


def agent_generate_and_self_verify_sql(
    problem_stmt: GoldProblemStatement,
    schema_understanding: SchemaSemanticUnderstanding,
    error_feedback: Optional[str] = None
) -> VerifiedSqlSolution:
    """Step 3: Generate SQL and self-verify against known column context traps."""
    prompt = f"""
You are an expert PostgreSQL 16 Analytics Engineer.
Generate the analytical SELECT query to solve this business problem:
{problem_stmt.model_dump_json(indent=2)}

Column Semantics, Types, & Real Distinct Sample Values:
{schema_understanding.model_dump_json(indent=2)}

{f"PREVIOUS RUN FAILED WITH THESE METRIC VIOLATIONS: {error_feedback}. FIX THIS IN THE SQL!" if error_feedback else ""}

OPERATIONAL SQL RULES:
1. Source table is `silver.cleaned_tickets`.
2. DYNAMIC DATA GROUNDING (CRITICAL):
   - When writing WHERE clauses or CASE statements on categorical columns, ALWAYS inspect the `sample_values` in the schema context.
   - Match the EXACT casing, spelling, and format found in `sample_values` (e.g., do not write Title Case if samples are ALL CAPS, or vice versa).
3. STRICT COLUMN NAMING CONVENTION:
   - All column aliases MUST be strictly lowercase snake_case (e.g., `total_reported_tickets`, `avg_resolution_time_hours`).
   - NEVER use spaces or capital letters in aliases.
4. Compute `incident_year` as `EXTRACT(YEAR FROM created_at)::INT`. Filter `WHERE created_at IS NOT NULL`.
5. If computing resolution time or duration:
   - YOU MUST FILTER: `is_temporal_anomaly = FALSE AND status IN ('Resolved', 'Closed') AND resolved_at IS NOT NULL`.
   - Never allow unresolved tickets or temporal anomalies into duration averages!
6. If computing net tickets:
   - Gross tickets: `COUNT(ticket_id)`
   - Net actionable tickets: `COUNT(*) FILTER (WHERE NOT is_duplicate)`
7. Filter test rows where appropriate: `canonical_category != 'Unassigned'`.
8. Return ONLY the SELECT statement inside `raw_query_sql`. Do not include CREATE TABLE or CREATE VIEW.
"""
    return invoke_gemini_structured(VerifiedSqlSolution, prompt, temperature=0.0)


def execute_dry_run_and_extract_stats(target_name: str, select_sql: str) -> Dict[str, Any]:
    """Step 4: Execute query into staging and compute statistical distribution of numeric columns."""
    target_name = validate_gold_target(target_name)
    select_sql = validate_gold_select(select_sql)
    clean_target = target_name.replace("gold.", "")
    staging_table = f"gold._staging_{clean_target}"
    
    with gold_engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {staging_table};"))
        conn.execute(text(f"CREATE TABLE {staging_table} AS {select_sql};"))
        
        cols_query = text(f"""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_schema = 'gold' AND table_name = '_staging_{clean_target}';
        """)
        cols = conn.execute(cols_query).fetchall()
        
        row_count = conn.execute(text(f"SELECT COUNT(*) FROM {staging_table};")).scalar() or 0
        if row_count == 0:
            return {"row_count": 0, "numeric_stats": {}}

        stats = {}
        for col_name, data_type in cols:
            if data_type in ['integer', 'bigint', 'smallint', 'numeric', 'double precision', 'real']:
                safe_col = f'"{col_name}"'
                stat_sql = text(f"""
                    SELECT 
                        MIN({safe_col}) AS col_min,
                        MAX({safe_col}) AS col_max,
                        AVG({safe_col}) AS col_avg,
                        COUNT(*) FILTER (WHERE {safe_col} < 0) AS negative_count,
                        COUNT(*) FILTER (WHERE {safe_col} IS NULL) AS null_count
                    FROM {staging_table};
                """)
                res = conn.execute(stat_sql).mappings().one()
                stats[col_name] = {
                    "min": float(res["col_min"]) if res["col_min"] is not None else None,
                    "max": float(res["col_max"]) if res["col_max"] is not None else None,
                    "avg": float(res["col_avg"]) if res["col_avg"] is not None else None,
                    "negative_count": int(res["negative_count"]),
                    "null_count": int(res["null_count"])
                }

    return {
        "staging_table": staging_table,
        "row_count": row_count,
        "numeric_stats": stats
    }


def agent_audit_gold_stats(stats_payload: Dict[str, Any]) -> StatAuditResult:
    """Step 5: Agent inspects empirical statistics and verifies physical possibility."""
    prompt = f"""
You are an expert Data Quality Auditor reviewing the statistical output of a newly computed Gold data mart.
Inspect the following column statistics extracted from PostgreSQL:
{json.dumps(stats_payload, indent=2, default=str)}

EVALUATION RULES:
1. Any duration/resolution time (e.g. `avg_resolution_time_hours`) CANNOT be negative. If `min < 0`, that is an automatic failure!
2. Any cost metric (e.g. `total_cost_usd`, `avg_cost_per_incident_usd`) CANNOT be negative.
3. Row count cannot be 0.
4. Net actionable incidents cannot be greater than gross incidents.

Return whether the dataset is statistically sound. If violations exist, specify exact details and SQL fix.
"""
    return invoke_gemini_structured(StatAuditResult, prompt, temperature=0.0)


def promote_staging_to_production_view(target_name: str, select_sql: str) -> int:
    """Step 6: Promote verified SQL into production Materialized View."""
    target_name = validate_gold_target(target_name)
    select_sql = validate_gold_select(select_sql)
    with admin_engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS gold;"))
        conn.execute(text("SET LOCAL ROLE medallion_gold;"))
        conn.execute(text(f"DROP MATERIALIZED VIEW IF EXISTS {target_name};"))
        conn.execute(text(f"CREATE MATERIALIZED VIEW {target_name} AS {select_sql};"))
        row_count = conn.execute(text(f"SELECT COUNT(*) FROM {target_name};")).scalar() or 0
    return row_count
