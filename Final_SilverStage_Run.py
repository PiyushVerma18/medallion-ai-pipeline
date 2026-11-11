import json
from src.pipeline.silver_graph import build_silver_pipeline_graph

if __name__ == "__main__":
    print("\n" + "=" * 90)
    print("🚀 LAUNCHING AGENTIC MEDALLION PIPELINE (LANGGRAPH ORCHESTRATION)")
    print("=" * 90)

    # 1. Compile Graph
    pipeline = build_silver_pipeline_graph()

    # 2. Initial State Input
    initial_state = {
        "bronze_schema": "bronze",
        "bronze_table": "raw_tickets",
        "silver_schema": "silver",
        "silver_table": "cleaned_tickets",
        "llm_batch_limit": 30,
        "profile_data": None,
        "dq_audit_report": None,
        "swapped_candidates_count": 0,
        "distinct_categories_count": 0,
        "extracted_descriptions_stats": {},
        "extracted_resolutions_count": 0,
        "pipeline_status": "PENDING",
        "execution_logs": []
    }

    # 3. Execute Graph
    final_output = pipeline.invoke(initial_state)

    # 4. Display Executive Scorecard
    metrics = final_output["metrics"]
    print("\n" + "=" * 90)
    print("📊 SILVER MATERIALIZATION EXECUTIVE SCORECARD")
    print("=" * 90)
    print(f"Status                           : {final_output['pipeline_status']}")
    print(f"Bronze Raw Records Ingested      : {metrics.bronze_total_rows:,}")
    print(f"Silver Clean Records Materialized: {metrics.silver_total_rows:,}")
    print(f"Inverted Columns Swapped         : {metrics.swapped_repaired_count:,}")
    print(f"Taxonomy Classes Standardized    : {metrics.taxonomy_classes_mapped:,}")
    print(f"Duplicate Tickets Tracked        : {metrics.duplicates_flagged:,}")
    print(f"Temporal Inversions Flagged      : {metrics.temporal_anomalies_flagged:,}")
    print("-" * 90)
    print("Pipeline Execution History:")
    for i, log in enumerate(final_output["execution_logs"], start=1):
        print(f"  [{i}] {log}")
    print("=" * 90 + "\n")