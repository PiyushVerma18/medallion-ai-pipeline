from src.pipeline.gold_closed_loop_graph import build_multi_domain_closed_loop_pipeline, DomainConfig

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

if __name__ == "__main__":
    print("\n" + "=" * 90)
    print("🚀 RUNNING MULTI-DOMAIN CLOSED-LOOP GOLD PIPELINE (4 MODELS)")
    print("=" * 90)

    pipeline = build_multi_domain_closed_loop_pipeline()

    initial_state = {
        "columns_list": SILVER_COLUMNS,
        "domains": DOMAINS,
        "max_healing_retries": 3,
        "schema_understanding": None,
        "models": [],
        "current_model_idx": 0,
        "execution_history": []
    }

    final_state = pipeline.invoke(initial_state)

    print("\n" + "=" * 90)
    print("🏁 FINAL MULTI-DOMAIN SCORECARD")
    print("=" * 90)
    for idx, m in enumerate(final_state["models"], start=1):
        icon = "✔" if m.is_promoted else "✖"
        print(f"{icon} Model {idx}: {m.problem.target_table_name:<45} | Rows: {m.final_row_count:>5,} | Domain: {m.domain_name[:30]}...")
    print("=" * 90 + "\n")