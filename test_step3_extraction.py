from sqlalchemy import text
from src.db import engine
from src.agents.semantic_extract import extract_description_entities, extract_resolution_entities

if __name__ == "__main__":
    print("=" * 80)
    print("TESTING AGENT 2 - STEP 3: HYBRID DUAL-COLUMN ENTITY EXTRACTION")
    print("=" * 80)

    # 1. Run description entity extraction (Regex Tier 1 + LLM Tier 2 Batch)
    desc_stats = extract_description_entities(llm_batch_limit=30)
    print(f"\n[Description Processing Stats]: {desc_stats}")

    # 2. Run resolution notes extraction (High-precision deterministic)
    res_count = extract_resolution_entities()
    print(f"[Resolution Notes Processed]: {res_count} distinct notes cached.")

    # 3. Inspect sample description extractions directly from PostgreSQL
    print("\n" + "-" * 80)
    print("SAMPLE EXTRACTED DESCRIPTION ENTITIES (from silver.dim_description_entities):")
    print("-" * 80)
    with engine.connect() as conn:
        samples = conn.execute(text("""
            SELECT raw_text, extracted_asset_type, extracted_asset_id, extracted_floor, extracted_room, source_method
            FROM silver.dim_description_entities
            WHERE extracted_floor IS NOT NULL OR extracted_asset_type IS NOT NULL OR extracted_room IS NOT NULL
            LIMIT 6;
        """)).mappings().all()

        for s in samples:
            print(f"RAW TEXT   : {s['raw_text']}")
            print(f"  Floor: {s['extracted_floor']} | Room: {s['extracted_room']} | Asset: {s['extracted_asset_type']} ({s['extracted_asset_id']}) [via {s['source_method']}]\n")

    # 4. Inspect sample resolution notes extractions directly from PostgreSQL
    print("-" * 80)
    print("SAMPLE EXTRACTED RESOLUTION ENTITIES (from silver.dim_resolution_entities):")
    print("-" * 80)
    with engine.connect() as conn:
        res_samples = conn.execute(text("""
            SELECT raw_text, is_duplicate, duplicate_of_ticket_id, root_cause, resolution_action, hours_on_site
            FROM silver.dim_resolution_entities
            WHERE is_duplicate = TRUE OR root_cause IS NOT NULL OR hours_on_site IS NOT NULL
            LIMIT 6;
        """)).mappings().all()

        for r in res_samples:
            print(f"RAW NOTE   : {r['raw_text']}")
            print(f"  Duplicate: {r['is_duplicate']} (Master: {r['duplicate_of_ticket_id']}) | Root Cause: {r['root_cause']} | Action: {r['resolution_action']} | Hours: {r['hours_on_site']}\n")