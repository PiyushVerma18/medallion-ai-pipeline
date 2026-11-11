import json
import os
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field
from langchain_google_genai import ChatGoogleGenerativeAI
from src.config import GEMINI_API_KEY, GEMINI_MODEL


class GoldModelSpec(BaseModel):
    model_name: str = Field(
        description="PostgreSQL table or materialized view name (prefixed with 'gold.')"
    )
    materialization_type: str = Field(
        description="Either 'TABLE' or 'MATERIALIZED VIEW'"
    )
    target_stakeholder: str = Field(
        description="Primary business consumer (e.g., VP of Real Estate, Reliability Engineer, Procurement Lead)"
    )
    business_objective: str = Field(
        description="Operational problem solved and decisions enabled by this model"
    )
    grain: str = Field(
        description="Exact grain of 1 row (must incorporate annual cohort: '... per incident_year')"
    )
    dimensions: List[str] = Field(description="Dimensions in GROUP BY")
    metrics: List[str] = Field(description="Metrics calculated")
    justification_for_readme: str = Field(
        description="Analytical justification for why this model is critical for the README"
    )
    ddl_and_dml_sql: str = Field(
        description="PostgreSQL 16 executable SQL to create and populate the table or view idempotently"
    )


class GoldDomainProposal(BaseModel):
    domain_name: str = Field(description="Name of the business domain")
    executive_overview: str = Field(description="Architectural rationale for this domain's models")
    proposed_models: List[GoldModelSpec] = Field(
        description="Exactly 2 high-impact, business-ready models at the annual grain"
    )


def run_gold_design_agent(
    silver_schema_summary: Dict[str, Any],
    business_domain_name: str,
    business_domain_narrative: str
) -> GoldDomainProposal:
    """
    Gold Layer Design Agent:
    Takes Silver schema and a business domain narrative, producing 2 annual-grain models.
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not configured.")

    os.environ["GOOGLE_API_KEY"] = GEMINI_API_KEY
    llm = ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        google_api_key=GEMINI_API_KEY,
        temperature=0.1
    ).with_structured_output(GoldDomainProposal)

    system_prompt = """
You are a Principal Analytics Engineer and Enterprise Data Warehouse Architect.
Your task is to design exactly 2 high-impact Gold-layer analytical models (Tables or Materialized Views) on top of PostgreSQL 16 table `silver.cleaned_tickets`.

CRITICAL ARCHITECTURAL DIRECTIVES:
1. YEAR-LEVEL AGGREGATION ONLY:
   - Group temporal metrics by year: `EXTRACT(YEAR FROM created_at)::INT AS incident_year`.
   - Do NOT use monthly or daily grains, as they cause excessive data fragmentation.
   - Filter `WHERE created_at IS NOT NULL`.

2. DOMAIN-ALIGNED COVERAGE:
   - Tailor both models directly to the provided business domain narrative and stakeholders.
   - Propose exactly 2 models addressing complementary analytical questions in this domain.

3. DATA GOVERNANCE & INTEGRITY RULES:
   - Differentiate Gross Volume (`COUNT(ticket_id)`) from Net Actionable Incidents (`COUNT(*) FILTER (WHERE NOT is_duplicate)`).
   - In financial metrics (`cost`), exclude nulls: `SUM(cost)`, `AVG(cost)`.
   - Exclude unassigned/test rows where appropriate (e.g. `WHERE canonical_category != 'Unassigned'`).
   - For asset models, enforce `WHERE extracted_asset_type IS NOT NULL`.
   - For workforce models, enforce `WHERE assigned_to IS NOT NULL`.

4. PRODUCTION POSTGRESQL 16 EXECUTION:
   - Schema prefix must be `gold.`
   - Ensure idempotency:
     For Materialized Views: `CREATE SCHEMA IF NOT EXISTS gold; DROP MATERIALIZED VIEW IF EXISTS gold.<name>; CREATE MATERIALIZED VIEW gold.<name> AS SELECT ...;`
     For Tables: `CREATE SCHEMA IF NOT EXISTS gold; CREATE TABLE IF NOT EXISTS gold.<name> (...); TRUNCATE TABLE gold.<name>; INSERT INTO gold.<name> SELECT ...;`
"""

    payload = {
        "business_domain_name": business_domain_name,
        "business_domain_narrative": business_domain_narrative,
        "silver_schema_summary": silver_schema_summary
    }

    user_prompt = (
        f"Design the Gold analytical models for the '{business_domain_name}' domain at the annual grain. "
        f"Return the proposal with production SQL:\n\n{json.dumps(payload, indent=2, default=str)}"
    )

    return llm.invoke([
        ("system", system_prompt),
        ("user", user_prompt)
    ])