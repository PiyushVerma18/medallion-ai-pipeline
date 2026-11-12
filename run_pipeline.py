import os
import sys
import time
from sqlalchemy import text

from src.db import engine
from src.pipeline.bronze_pipeline import ingest_bronze
from src.pipeline.silver_graph import build_silver_pipeline_graph
from src.pipeline.stage_logging import configure_pipeline_logging, log_stage
from src.pipeline.gold_closed_loop_graph import (
    build_multi_domain_closed_loop_pipeline,
    DomainConfig,
)

SILVER_COLUMNS = [
    "ticket_id", "_source_file", "_ingested_at", "_silver_processed_at", "_row_hash",
    "created_at", "resolved_at", "is_temporal_anomaly", "raw_category", "canonical_category",
    "priority", "status", "building", "raw_description", "is_field_swapped",
    "extracted_asset_type", "extracted_asset_id", "extracted_floor", "extracted_room",
    "submitted_by", "assigned_to", "resolution_notes", "is_duplicate",
    "duplicate_of_ticket_id", "root_cause", "resolution_action", "hours_on_site",
    "cost", "sla_hours"
]

DOMAINS = [
    DomainConfig(
        domain_name="Corporate Real Estate & Facilities Operations (CRE / FM)",
        narrative="""
        Focus: Campus-wide physical plant health, regional/building OPEX allocation, 
        trade discipline budget distribution, duplicate ticket noise reduction, and capital expenditure (CapEx) planning.
        Stakeholders: VP of Real Estate, Facilities Operations Directors, Corporate Financial Controllers.
        Key Questions: Which buildings and trade disciplines drain the budget? How much noise comes from duplicate tickets? 
        Which facilities have escalating critical risks warranting major renovations?
        """
    ),
    DomainConfig(
        domain_name="Asset Reliability & Sourcing Performance (Maintenance Engineering)",
        narrative="""
        Focus: Physical equipment failure diagnostics, bad-actor equipment tracking, 
        root causes, on-site labor hours, and contractor versus in-house workforce efficiency.
        Stakeholders: Chief Reliability Engineer, Maintenance Supervisors, Procurement / Vendor Contract Leads.
        Key Questions: Which physical assets fail most frequently? What are the primary root causes? 
        How do contracted vendors compare to internal technicians on first-time fix rates, average resolution duration, and total labor hours?
        """
    )
]

@log_stage("pipeline.end_to_end")
def _run_pipeline():
    start_time = time.time()
    print("\n" + "=" * 90)
    print("🚀 LAUNCHING END-TO-END AUTONOMOUS MEDALLION PIPELINE (BRONZE -> SILVER -> GOLD)")
    print("=" * 90)

    # ---------------------------------------------------------
    # 1. BRONZE LAYER INGESTION
    # ---------------------------------------------------------
    print("\n[Phase 1] Ingesting Raw Tickets into Bronze Layer...")
    bronze_metrics = ingest_bronze("data/raw_tickets.csv")

    # ---------------------------------------------------------
    # 2. SILVER LAYER PROCESSING
    # ---------------------------------------------------------
    print("\n[Phase 2] Executing Silver Processing LangGraph (Agents 1 & 2)...")
    silver_app = build_silver_pipeline_graph()
    silver_initial_state = {
        "bronze_schema": "bronze",
        "bronze_table": "raw_tickets",
        "silver_schema": "silver",
        "silver_table": "cleaned_tickets",
        "llm_batch_limit": 40,
        "profile_data": None,
        "dq_audit_report": None,
        "swapped_candidates_count": 0,
        "distinct_categories_count": 0,
        "extracted_descriptions_stats": {},
        "extracted_resolutions_count": 0,
        "metrics": None,
        "pipeline_status": "PENDING",
        "execution_logs": []
    }
    silver_final_state = silver_app.invoke(silver_initial_state)

    # ---------------------------------------------------------
    # 3. GOLD LAYER CLOSED-LOOP AGENT
    # ---------------------------------------------------------
    print("\n[Phase 3] Executing Multi-Domain Closed-Loop Gold Agent (Agent 3)...")
    gold_app = build_multi_domain_closed_loop_pipeline()
    gold_initial_state = {
        "columns_list": SILVER_COLUMNS,
        "domains": DOMAINS,
        "max_healing_retries": 3,
        "schema_understanding": None,
        "models": [],
        "current_model_idx": 0,
        "execution_history": []
    }
    gold_final_state = gold_app.invoke(gold_initial_state)

    total_time = time.time() - start_time

    # ---------------------------------------------------------
    # 4. FINAL OBSERVABILITY SCORECARD
    # ---------------------------------------------------------
    print("\n" + "=" * 90)
    print("📊 END-TO-END PIPELINE OBSERVABILITY & SLA SCORECARD")
    print("=" * 90)
    print(f"Total Execution Runtime     : {total_time:.2f} seconds")
    print(f"Bronze Raw Records Ingested : {bronze_metrics['total_bronze_records']:,}")
    print(f"Silver Validated Records    : {silver_final_state['metrics'].silver_total_rows:,}")
    print(f"  • Inverted Fields Repaired: {silver_final_state['metrics'].swapped_repaired_count:,}")
    print(f"  • Duplicate Tickets Marked: {silver_final_state['metrics'].duplicates_flagged:,}")
    print(f"  • Temporal Anomalies Flagged: {silver_final_state['metrics'].temporal_anomalies_flagged:,}")
    print("-" * 90)
    print("PRODUCTION GOLD DATA MARTS:")
    for idx, m in enumerate(gold_final_state["models"], start=1):
        icon = "✔" if m.is_promoted else "✖"
        row_c = getattr(m, "final_row_count", getattr(m, "row_count", 0))
        print(f"  {icon} Model {idx}: {m.problem.target_table_name:<46} | Rows: {row_c:>5,} | Domain: {m.domain_name[:28]}...")
    print("=" * 90 + "\n")

def main():
    configure_pipeline_logging()
    _run_pipeline()


if __name__ == "__main__":
    main()
