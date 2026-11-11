import operator
from typing import Dict, Any
from sqlalchemy import text
from langgraph.graph import StateGraph, START, END

from src.db import engine
from src.agents.catalog import TICKETS_CATALOG
from src.agents.generic_profiler import profile_catalog_dataset
from src.agents.data_quality import run_generalized_dq_agent
from src.agents.semantic_clean import (
    init_semantic_tables,
    run_semantic_swap_agent,
    run_semantic_taxonomy_agent
)
from src.agents.semantic_extract import (
    init_entity_cache,
    extract_description_entities,
    extract_resolution_entities
)
from src.pipeline.graph_state import PipelineState, SilverPipelineMetrics


# =========================================================================
# Node 1: Catalog-Driven Bronze Profiler
# =========================================================================
def node_profile_bronze(state: PipelineState) -> Dict[str, Any]:
    print("\n" + "=" * 80)
    print("▶ [Step 1/6: LangGraph Node] Running Catalog-Driven Profiler on Bronze...")
    print("=" * 80)

    profile_data = profile_catalog_dataset(
        catalog=TICKETS_CATALOG,
        table_schema=state["bronze_schema"],
        table_name=state["bronze_table"]
    )

    log_entry = (
        f"Profiled {profile_data['total_rows']} raw records. "
        f"Violations detected: {len(profile_data['cross_column_violations'])} cross-column constraints."
    )
    print(f"✔ {log_entry}")

    return {
        "profile_data": profile_data,
        "execution_logs": state.get("execution_logs", []) + [log_entry]
    }


# =========================================================================
# Node 2: Data Quality Agent (Agent 1)
# =========================================================================
def node_dq_agent(state: PipelineState) -> Dict[str, Any]:
    print("\n" + "=" * 80)
    print("▶ [Step 2/6: LangGraph Node] Invoking Data Quality Agent (Gemini)...")
    print("=" * 80)

    report = run_generalized_dq_agent(
        catalog=TICKETS_CATALOG,
        empirical_profile=state["profile_data"]
    )

    log_entry = (
        f"Agent 1 generated {len(report.column_rules)} column rules and "
        f"{len(report.table_integrity_rules)} table integrity constraints."
    )
    print(f"✔ {log_entry}")

    return {
        "dq_audit_report": report,
        "execution_logs": state.get("execution_logs", []) + [log_entry]
    }


# =========================================================================
# Node 3: Semantic Field-Swap Resolution (Agent 2 - Step 1)
# =========================================================================
def node_semantic_swap(state: PipelineState) -> Dict[str, Any]:
    print("\n" + "=" * 80)
    print("▶ [Step 3/6: LangGraph Node] Detecting & Semantically Resolving Field Swaps...")
    print("=" * 80)

    swap_resolutions = run_semantic_swap_agent()
    swapped_count = sum(1 for r in swap_resolutions if r.is_swapped)

    log_entry = f"Agent 2 evaluated {len(swap_resolutions)} candidate rows. Realigned {swapped_count} inverted columns."
    print(f"✔ {log_entry}")

    return {
        "swapped_candidates_count": swapped_count,
        "execution_logs": state.get("execution_logs", []) + [log_entry]
    }


# =========================================================================
# Node 4: Semantic Taxonomy Normalization (Agent 2 - Step 2)
# =========================================================================
def node_semantic_taxonomy(state: PipelineState) -> Dict[str, Any]:
    print("\n" + "=" * 80)
    print("▶ [Step 4/6: LangGraph Node] Normalizing Category Taxonomy via LLM...")
    print("=" * 80)

    mappings = run_semantic_taxonomy_agent()
    log_entry = f"Agent 2 mapped {len(mappings)} unique raw category tags into the 11-discipline canonical taxonomy."
    print(f"✔ {log_entry}")

    return {
        "distinct_categories_count": len(mappings),
        "execution_logs": state.get("execution_logs", []) + [log_entry]
    }


# =========================================================================
# Node 5: Dual-Column Entity Extraction (Agent 2 - Step 3)
# =========================================================================
def node_semantic_extraction(state: PipelineState) -> Dict[str, Any]:
    # ...
    desc_stats = extract_description_entities(batch_size=40, max_batches=None)
    res_stats = extract_resolution_entities()

    # Calculate total if dict, else use as int
    if isinstance(res_stats, dict):
        res_total = res_stats.get("regex_resolved", 0) + res_stats.get("llm_resolved", 0)
    else:
        res_total = res_stats

    log_entry = (
        f"Entities cached: Descriptions ({desc_stats['regex_resolved']} via Regex, {desc_stats['llm_resolved']} via LLM), "
        f"Resolution Notes ({res_total} total notes)."
    )
    print(f"✔ {log_entry}")

    return {
        "extracted_descriptions_stats": desc_stats,
        "extracted_resolutions_count": res_total,  # <-- Return the integer total here
        "execution_logs": state.get("execution_logs", []) + [log_entry]
    }


# =========================================================================
# Node 6: Silver Materialization (PostgreSQL DDL/DML Window Deduplication)
# =========================================================================
def node_materialize_silver(state: PipelineState) -> Dict[str, Any]:
    print("\n" + "=" * 80)
    print("▶ [Step 6/6: LangGraph Node] Materializing Final Silver Table...")
    print("=" * 80)

    # Initialize Table
    silver_ddl = f"""
    CREATE SCHEMA IF NOT EXISTS {state['silver_schema']};
    CREATE TABLE IF NOT EXISTS {state['silver_schema']}.{state['silver_table']} (
        ticket_id VARCHAR(50) PRIMARY KEY,
        _source_file VARCHAR(255) NOT NULL,
        _ingested_at TIMESTAMPTZ NOT NULL,
        _silver_processed_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
        _row_hash VARCHAR(64) NOT NULL,
        created_at TIMESTAMP,
        resolved_at TIMESTAMP,
        is_temporal_anomaly BOOLEAN NOT NULL DEFAULT FALSE,
        raw_category TEXT,
        canonical_category VARCHAR(50) NOT NULL,
        priority VARCHAR(20) NOT NULL,
        status VARCHAR(30) NOT NULL,
        building VARCHAR(100),
        raw_description TEXT,
        is_field_swapped BOOLEAN NOT NULL DEFAULT FALSE,
        extracted_asset_type VARCHAR(50),
        extracted_asset_id VARCHAR(50),
        extracted_floor VARCHAR(20),
        extracted_room VARCHAR(100),
        submitted_by VARCHAR(100),
        assigned_to VARCHAR(100),
        resolution_notes TEXT,
        is_duplicate BOOLEAN NOT NULL DEFAULT FALSE,
        duplicate_of_ticket_id VARCHAR(50),
        root_cause VARCHAR(100),
        resolution_action VARCHAR(50),
        hours_on_site NUMERIC(5,2),
        cost NUMERIC(10,2),
        sla_hours NUMERIC(6,1)
    );
    """

    # Transform & Hydrate with Window Deduplication
    materialization_sql = f"""
    INSERT INTO {state['silver_schema']}.{state['silver_table']} (
        ticket_id, _source_file, _ingested_at, _silver_processed_at, _row_hash,
        created_at, resolved_at, is_temporal_anomaly,
        raw_category, canonical_category, priority, status, building,
        raw_description, is_field_swapped,
        extracted_asset_type, extracted_asset_id, extracted_floor, extracted_room,
        submitted_by, assigned_to,
        resolution_notes, is_duplicate, duplicate_of_ticket_id, root_cause, resolution_action, hours_on_site,
        cost, sla_hours
    )
    WITH staged AS (
        SELECT
            -- 1. Idempotent Ticket ID
            CASE
                WHEN TRIM(b.ticket_id) IS NULL OR TRIM(b.ticket_id) = '' THEN 'TKT-SYNTH-' || SUBSTRING(b._row_hash, 1, 8)
                WHEN TRIM(b.ticket_id) !~ '^TKT-[0-9]+$' THEN 'TKT-SYNTH-' || SUBSTRING(b._row_hash, 1, 8)
                ELSE TRIM(b.ticket_id)
            END AS clean_ticket_id,

            b._source_file,
            b._ingested_at,
            b._row_hash,

            -- 2. Complete Timestamp Parser: created_at
            CASE
                WHEN TRIM(b.created_at) IN ('00/00/0000', 'asap', 'not a date', 'TBD') THEN NULL
                WHEN TRIM(b.created_at) ~ '^[0-9]{{10}}$' THEN TO_TIMESTAMP(TRIM(b.created_at)::BIGINT)
                WHEN TRIM(b.created_at) ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}[T ][0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}}' THEN REPLACE(TRIM(b.created_at), 'T', ' ')::TIMESTAMP
                WHEN TRIM(b.created_at) ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$' THEN TO_DATE(TRIM(b.created_at), 'YYYY-MM-DD')::TIMESTAMP
                WHEN TRIM(b.created_at) ~* '^[0-9]{{2}}/[0-9]{{2}}/[0-9]{{4}} [0-9]{{2}}:[0-9]{{2}} (AM|PM)$' THEN TO_TIMESTAMP(TRIM(b.created_at), 'MM/DD/YYYY HH12:MI AM')
                WHEN TRIM(b.created_at) ~ '^[0-9]{{2}}/[0-9]{{2}}/[0-9]{{4}}$' THEN TO_DATE(TRIM(b.created_at), 'MM/DD/YYYY')::TIMESTAMP
                WHEN TRIM(b.created_at) ~ '^[0-9]{{2}}-[A-Za-z]{{3}}-[0-9]{{4}} [0-9]{{2}}:[0-9]{{2}}$' THEN TO_TIMESTAMP(TRIM(b.created_at), 'DD-Mon-YYYY HH24:MI')
                WHEN TRIM(b.created_at) ~ '^[0-9]{{2}}-[0-9]{{2}}-[0-9]{{4}} [0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}}$' THEN TO_TIMESTAMP(TRIM(b.created_at), 'MM-DD-YYYY HH24:MI:SS')
                ELSE NULL
            END AS parsed_created_at,

            -- 3. Complete Timestamp Parser: resolved_at
            CASE
                WHEN TRIM(b.resolved_at) IN ('???', 'never', 'pending', 'TBD', '00/00/0000') THEN NULL
                WHEN TRIM(b.resolved_at) ~ '^[0-9]{{10}}$' THEN TO_TIMESTAMP(TRIM(b.resolved_at)::BIGINT)
                WHEN TRIM(b.resolved_at) ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}[T ][0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}}' THEN REPLACE(TRIM(b.resolved_at), 'T', ' ')::TIMESTAMP
                WHEN TRIM(b.resolved_at) ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$' THEN TO_DATE(TRIM(b.resolved_at), 'YYYY-MM-DD')::TIMESTAMP
                WHEN TRIM(b.resolved_at) ~* '^[0-9]{{2}}/[0-9]{{2}}/[0-9]{{4}} [0-9]{{2}}:[0-9]{{2}} (AM|PM)$' THEN TO_TIMESTAMP(TRIM(b.resolved_at), 'MM/DD/YYYY HH12:MI AM')
                WHEN TRIM(b.resolved_at) ~ '^[0-9]{{2}}/[0-9]{{2}}/[0-9]{{4}}$' THEN TO_DATE(TRIM(b.resolved_at), 'MM/DD/YYYY')::TIMESTAMP
                WHEN TRIM(b.resolved_at) ~ '^[0-9]{{2}}-[A-Za-z]{{3}}-[0-9]{{4}} [0-9]{{2}}:[0-9]{{2}}$' THEN TO_TIMESTAMP(TRIM(b.resolved_at), 'DD-Mon-YYYY HH24:MI')
                WHEN TRIM(b.resolved_at) ~ '^[0-9]{{2}}-[0-9]{{2}}-[0-9]{{4}} [0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}}$' THEN TO_TIMESTAMP(TRIM(b.resolved_at), 'MM-DD-YYYY HH24:MI:SS')
                ELSE NULL
            END AS parsed_resolved_at,

            -- 4. Swapped Text Alignment
            COALESCE(sw.is_swapped, FALSE) AS is_field_swapped,
            COALESCE(sw.clean_category, TRIM(b.category)) AS resolved_category,
            COALESCE(sw.clean_description, TRIM(b.description)) AS resolved_description,

            -- 5. Canonical Category Taxonomy
            COALESCE(cat.canonical_category, 'General Maintenance') AS clean_canonical_category,

            -- 6. Canonical Priority Enum
            CASE
                WHEN LOWER(TRIM(b.priority)) IN ('crit', 'critical', 'urgent!!!', 'asap') THEN 'CRITICAL'
                WHEN LOWER(TRIM(b.priority)) IN ('hi', 'high') THEN 'HIGH'
                WHEN LOWER(TRIM(b.priority)) IN ('med', 'medium', 'normal') THEN 'MEDIUM'
                WHEN LOWER(TRIM(b.priority)) IN ('lo', 'low') THEN 'LOW'
                ELSE 'MEDIUM'
            END AS clean_priority,

            -- 7. Canonical Lifecycle Status
            CASE
                WHEN LOWER(TRIM(b.status)) = 'open' THEN 'Open'
                WHEN LOWER(TRIM(b.status)) = 'in progress' THEN 'In Progress'
                WHEN LOWER(TRIM(b.status)) = 'pending vendor' THEN 'Pending Vendor'
                WHEN LOWER(TRIM(b.status)) = 'escalated' THEN 'Escalated'
                WHEN LOWER(TRIM(b.status)) = 'resolved' THEN 'Resolved'
                WHEN LOWER(TRIM(b.status)) = 'closed' THEN 'Closed'
                ELSE 'Unassigned'
            END AS clean_status,

            -- 8. Clean Building
            CASE
                WHEN TRIM(b.building) IS NULL THEN NULL
                WHEN LOWER(TRIM(b.building)) IN ('???', 'unknown', 'test building') THEN NULL
                ELSE TRIM(b.building)
            END AS clean_building,

            -- 9. Description Entities
            de.extracted_asset_type,
            de.extracted_asset_id,
            de.extracted_floor,
            de.extracted_room,

            -- 10. Operational Stakeholders
            TRIM(b.submitted_by) AS clean_submitted_by,
            TRIM(b.assigned_to) AS clean_assigned_to,

            -- 11. Resolution Notes Entities
            TRIM(b.resolution_notes) AS clean_resolution_notes,
            COALESCE(re.is_duplicate, FALSE) AS is_duplicate,
            re.duplicate_of_ticket_id,
            re.root_cause,
            re.resolution_action,
            re.hours_on_site,

            -- 12. Sanitized Financials & SLAs
            CASE
                WHEN TRIM(b.cost) IS NULL OR TRIM(b.cost) IN ('TBD', 'error', '-1', '-999') THEN NULL
                WHEN TRIM(REPLACE(REPLACE(b.cost, '$', ''), ',', '')) ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN
                    CASE
                        WHEN (TRIM(REPLACE(REPLACE(b.cost, '$', ''), ',', ''))::NUMERIC) < 0 THEN NULL
                        ELSE TRIM(REPLACE(REPLACE(b.cost, '$', ''), ',', ''))::NUMERIC(10,2)
                    END
                ELSE NULL
            END AS clean_cost,

            CASE
                WHEN TRIM(b.sla_hours) IS NULL OR TRIM(b.sla_hours) IN ('999', '999.0', '-1', '-1.0', '0', '0.0') THEN NULL
                WHEN TRIM(b.sla_hours) ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN
                    CASE
                        WHEN (TRIM(b.sla_hours)::NUMERIC) <= 0 THEN NULL
                        ELSE TRIM(b.sla_hours)::NUMERIC(6,1)
                    END
                ELSE NULL
            END AS clean_sla_hours,

            -- Deduplication Window
            ROW_NUMBER() OVER (
                PARTITION BY (
                    CASE
                        WHEN TRIM(b.ticket_id) IS NULL OR TRIM(b.ticket_id) = '' THEN 'TKT-SYNTH-' || SUBSTRING(b._row_hash, 1, 8)
                        WHEN TRIM(b.ticket_id) !~ '^TKT-[0-9]+$' THEN 'TKT-SYNTH-' || SUBSTRING(b._row_hash, 1, 8)
                        ELSE TRIM(b.ticket_id)
                    END
                )
                ORDER BY b._ingested_at DESC, b._row_hash ASC
            ) AS dedup_rank

        FROM {state['bronze_schema']}.{state['bronze_table']} b
        LEFT JOIN silver.dim_field_swap_cache sw
            ON sw.ticket_id = COALESCE(b.ticket_id, 'SYNTH-' || SUBSTRING(b._row_hash, 1, 8))
        LEFT JOIN silver.dim_category_taxonomy cat
            ON cat.raw_category = COALESCE(sw.clean_category, TRIM(b.category))
        LEFT JOIN silver.dim_description_entities de
            ON de.text_hash = MD5(TRIM(b.description))
        LEFT JOIN silver.dim_resolution_entities re
            ON re.text_hash = MD5(TRIM(b.resolution_notes))
    )
    SELECT
        clean_ticket_id AS ticket_id,
        _source_file,
        _ingested_at,
        CURRENT_TIMESTAMP AS _silver_processed_at,
        _row_hash,
        parsed_created_at AS created_at,
        parsed_resolved_at AS resolved_at,
        CASE
            WHEN parsed_created_at IS NOT NULL 
             AND parsed_resolved_at IS NOT NULL 
             AND parsed_resolved_at < parsed_created_at 
            THEN TRUE 
            ELSE FALSE 
        END AS is_temporal_anomaly,
        resolved_category AS raw_category,
        clean_canonical_category AS canonical_category,
        clean_priority AS priority,
        clean_status AS status,
        clean_building AS building,
        resolved_description AS raw_description,
        is_field_swapped,
        extracted_asset_type,
        extracted_asset_id,
        extracted_floor,
        extracted_room,
        clean_submitted_by AS submitted_by,
        clean_assigned_to AS assigned_to,
        clean_resolution_notes AS resolution_notes,
        is_duplicate,
        duplicate_of_ticket_id,
        root_cause,
        resolution_action,
        hours_on_site,
        clean_cost AS cost,
        clean_sla_hours AS sla_hours
    FROM staged
    WHERE dedup_rank = 1
    ON CONFLICT (ticket_id) DO UPDATE SET
        _source_file = EXCLUDED._source_file,
        _silver_processed_at = CURRENT_TIMESTAMP,
        _row_hash = EXCLUDED._row_hash,
        created_at = EXCLUDED.created_at,
        resolved_at = EXCLUDED.resolved_at,
        is_temporal_anomaly = EXCLUDED.is_temporal_anomaly,
        raw_category = EXCLUDED.raw_category,
        canonical_category = EXCLUDED.canonical_category,
        priority = EXCLUDED.priority,
        status = EXCLUDED.status,
        building = EXCLUDED.building,
        raw_description = EXCLUDED.raw_description,
        is_field_swapped = EXCLUDED.is_field_swapped,
        extracted_asset_type = EXCLUDED.extracted_asset_type,
        extracted_asset_id = EXCLUDED.extracted_asset_id,
        extracted_floor = EXCLUDED.extracted_floor,
        extracted_room = EXCLUDED.extracted_room,
        submitted_by = EXCLUDED.submitted_by,
        assigned_to = EXCLUDED.assigned_to,
        resolution_notes = EXCLUDED.resolution_notes,
        is_duplicate = EXCLUDED.is_duplicate,
        duplicate_of_ticket_id = EXCLUDED.duplicate_of_ticket_id,
        root_cause = EXCLUDED.root_cause,
        resolution_action = EXCLUDED.resolution_action,
        hours_on_site = EXCLUDED.hours_on_site,
        cost = EXCLUDED.cost,
        sla_hours = EXCLUDED.sla_hours;
    """

    with engine.begin() as conn:
        conn.execute(text(silver_ddl))
        conn.execute(text(materialization_sql))

        # Query operational metrics
        total_silver = conn.execute(text(f"SELECT COUNT(*) FROM {state['silver_schema']}.{state['silver_table']};")).scalar() or 0
        temporal_anomalies = conn.execute(text(f"SELECT COUNT(*) FROM {state['silver_schema']}.{state['silver_table']} WHERE is_temporal_anomaly = TRUE;")).scalar() or 0
        duplicates = conn.execute(text(f"SELECT COUNT(*) FROM {state['silver_schema']}.{state['silver_table']} WHERE is_duplicate = TRUE;")).scalar() or 0
        swapped = conn.execute(text(f"SELECT COUNT(*) FROM {state['silver_schema']}.{state['silver_table']} WHERE is_field_swapped = TRUE;")).scalar() or 0

    res_count = state.get("extracted_resolutions_count", 0)
    if isinstance(res_count, dict):
        res_count = sum(res_count.values())
    
    desc_stats = state.get("extracted_descriptions_stats", {})
    desc_count = (
        desc_stats.get("regex_resolved", 0) + desc_stats.get("llm_resolved", 0)
        if isinstance(desc_stats, dict)
        else 0
    )

    metrics = SilverPipelineMetrics(
        bronze_total_rows=state["profile_data"]["total_rows"],
        silver_total_rows=total_silver,
        swapped_repaired_count=swapped,
        taxonomy_classes_mapped=state.get("distinct_categories_count", 0),
        description_entities_cached=desc_count,
        resolution_entities_cached=res_count,
        temporal_anomalies_flagged=temporal_anomalies,
        duplicates_flagged=duplicates
    )

    log_entry = f"Silver Table Materialized: {total_silver} rows clean and queryable."
    print(f"✔ {log_entry}")

    return {
        "metrics": metrics,
        "pipeline_status": "SUCCESS",
        "execution_logs": state.get("execution_logs", []) + [log_entry]
    }


# =========================================================================
# StateGraph Assembly
# =========================================================================
def build_silver_pipeline_graph() -> StateGraph:
    """Builds and compiles the end-to-end Medallion Silver LangGraph."""
    graph = StateGraph(PipelineState)

    # 1. Register Nodes
    graph.add_node("profile_bronze", node_profile_bronze)
    graph.add_node("dq_agent", node_dq_agent)
    graph.add_node("semantic_swap", node_semantic_swap)
    graph.add_node("semantic_taxonomy", node_semantic_taxonomy)
    graph.add_node("semantic_extraction", node_semantic_extraction)
    graph.add_node("materialize_silver", node_materialize_silver)

    # 2. Define Execution Edges
    graph.add_edge(START, "profile_bronze")
    graph.add_edge("profile_bronze", "dq_agent")
    graph.add_edge("dq_agent", "semantic_swap")
    graph.add_edge("semantic_swap", "semantic_taxonomy")
    graph.add_edge("semantic_taxonomy", "semantic_extraction")
    graph.add_edge("semantic_extraction", "materialize_silver")
    graph.add_edge("materialize_silver", END)

    return graph.compile()