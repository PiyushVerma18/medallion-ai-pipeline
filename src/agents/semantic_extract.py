import hashlib
import json
import re
import time
from typing import Dict, List, Optional
from pydantic import BaseModel, Field
from sqlalchemy import text
from langchain_google_genai import ChatGoogleGenerativeAI
from src.config import GEMINI_API_KEY, GEMINI_MODEL
from src.db import engine

# =========================================================================
# 1. Pydantic Schemas for Structured Output
# =========================================================================

class DescriptionEntities(BaseModel):
    item_id: int = Field(description="The integer index of the item from the input batch.")
    extracted_asset_type: Optional[str] = Field(
        None,
        description=(
            "Specific physical hardware, appliance, or fixture only "
            "(e.g., 'Elevator', 'Circuit Breaker', 'AC Unit', 'Sprinkler', 'Door', 'Printer', 'Toilet', 'Faucet', 'Pothole', 'Badge Reader'). "
            "NEVER return broad trade categories like 'Plumbing', 'Electrical', 'HVAC', or 'General Maintenance'."
        )
    )
    extracted_asset_id: Optional[str] = Field(
        None,
        description="Alphanumeric identifier or code (e.g., 'E-048', 'HP C-042', 'D-010', 'Unit E-009')."
    )
    extracted_floor: Optional[str] = Field(
        None,
        description="Specific floor number or range (e.g., '10', '3', '2-9'). Do NOT include the building name."
    )
    extracted_room: Optional[str] = Field(
        None,
        description="Specific room, suite, or interior area (e.g., 'server room 391', 'room 770', 'break room', 'restroom'). NEVER include facility or building names."
    )


class DescriptionBatchOutput(BaseModel):
    items: List[DescriptionEntities]


class RichResolutionEntities(BaseModel):
    item_id: int = Field(description="The integer index of the item from the input batch.")
    is_duplicate: bool = Field(False, description="True if the note explicitly states this is a duplicate ticket.")
    duplicate_of_ticket_id: Optional[str] = Field(None, description="Referenced master ticket ID (e.g. 'TKT-3098').")
    root_cause: Optional[str] = Field(None, description="Diagnosed cause of failure (e.g. 'faulty wiring', 'corrosion', 'power surge', 'normal wear', 'clogged filter').")
    resolution_action: Optional[str] = Field(None, description="Standard action tier: 'Fixed on site', 'Parts on order', 'Temporary fix', 'Could not reproduce', 'Vendor scheduled', 'Closed duplicate', 'Resolved by technician'.")
    hours_on_site: Optional[float] = Field(None, description="Labor hours spent on site if explicitly recorded.")
    servicing_vendor_or_tech: Optional[str] = Field(None, description="Company, crew, or technician name (e.g. 'CityWide Electric', 'PestPro Services', 'ABC Mechanical', 'overnight crew', 'Joe (in-house)').")
    replaced_component: Optional[str] = Field(None, description="Specific hardware part replaced (e.g. 'compressor', 'capacitor', 'thermostat', 'sensor', 'motor').")


class ResolutionBatchOutput(BaseModel):
    items: List[RichResolutionEntities]


# =========================================================================
# 2. Database DDL & Schema Migration
# =========================================================================

CACHE_TABLES_DDL = """
CREATE TABLE IF NOT EXISTS silver.dim_description_entities (
    text_hash VARCHAR(64) PRIMARY KEY,
    raw_text TEXT NOT NULL,
    extracted_asset_type VARCHAR(50),
    extracted_asset_id VARCHAR(50),
    extracted_floor VARCHAR(20),
    extracted_room VARCHAR(100),
    source_method VARCHAR(20) NOT NULL DEFAULT 'REGEX',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS silver.dim_resolution_entities (
    text_hash VARCHAR(64) PRIMARY KEY,
    raw_text TEXT NOT NULL,
    is_duplicate BOOLEAN NOT NULL DEFAULT FALSE,
    duplicate_of_ticket_id VARCHAR(50),
    root_cause VARCHAR(100),
    resolution_action VARCHAR(50),
    hours_on_site NUMERIC(5,2),
    servicing_vendor_or_tech VARCHAR(100),
    replaced_component VARCHAR(100),
    source_method VARCHAR(20) NOT NULL DEFAULT 'REGEX',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Ensure migration on existing tables
ALTER TABLE silver.dim_description_entities ALTER COLUMN text_hash TYPE VARCHAR(64);
ALTER TABLE silver.dim_resolution_entities ALTER COLUMN text_hash TYPE VARCHAR(64);
ALTER TABLE silver.dim_resolution_entities ADD COLUMN IF NOT EXISTS servicing_vendor_or_tech VARCHAR(100);
ALTER TABLE silver.dim_resolution_entities ADD COLUMN IF NOT EXISTS replaced_component VARCHAR(100);
"""


def init_entity_cache():
    """Initializes and migrates cache tables in PostgreSQL."""
    with engine.begin() as conn:
        conn.execute(text(CACHE_TABLES_DDL))


def md5(val: str) -> str:
    return hashlib.md5(val.strip().encode("utf-8")).hexdigest()


# =========================================================================
# 3. Tier 1: Fast Deterministic Regex Parsers
# =========================================================================

def fast_regex_extract_description(desc: str, building_name: Optional[str] = None) -> DescriptionEntities:
    t_hash = md5(desc)
    if not desc or len(desc.strip()) < 5:
        return DescriptionEntities(item_id=0)

    text_to_scan = desc
    if building_name:
        text_to_scan = re.sub(re.escape(building_name), '', text_to_scan, flags=re.I).strip()

    # 1. Floor Extraction
    floor = None
    multi_floor = re.search(r'\bfloors?\s+(\d+)\s+and\s+(\d+)\b', text_to_scan, re.I)
    if multi_floor:
        floor = f"{multi_floor.group(1)}-{multi_floor.group(2)}"
    else:
        floor_match = re.search(r'\b(?:floor\s+(\d+)|(\d+)\s+floor)\b', text_to_scan, re.I)
        if floor_match:
            floor = floor_match.group(1) or floor_match.group(2)

    # 2. Room Extraction
    room = None
    room_match = re.search(r'\b(?:room|suite|closet|dock)\s+([0-9]+[A-Za-z]?|[A-Za-z]-[0-9]+)\b', text_to_scan, re.I)
    if room_match:
        room = room_match.group(1)
    elif re.search(r'\b(bathroom|restroom|break room|cafeteria|lobby)\b', text_to_scan, re.I):
        area_match = re.search(r'\b(bathroom|restroom|break room|cafeteria|lobby)\b', text_to_scan, re.I)
        room = area_match.group(1).lower()

    # 3. Asset ID Extraction
    asset_id = None
    asset_id_match = re.search(
        r'\b(?:Unit\s+([A-Z]-[0-9]{3})|Model\s+([A-Za-z0-9-]+)|([A-Z]-[0-9]{3}))\b',
        text_to_scan,
        re.I
    )
    if asset_id_match:
        asset_id = asset_id_match.group(1) or asset_id_match.group(2) or asset_id_match.group(3)

    # 4. Asset Type Extraction
    asset_type = None
    asset_types_map = {
        "elevator": "Elevator",
        "fire extinguisher": "Fire Extinguisher",
        "fire alarm": "Fire Alarm",
        "sprinkler": "Sprinkler",
        "thermostat": "Thermostat",
        "ac unit": "AC Unit",
        "hvac": "HVAC Unit",
        "circuit breaker": "Circuit Breaker",
        "breaker": "Circuit Breaker",
        "printer": "Printer",
        "security camera": "Security Camera",
        "camera": "Security Camera",
        "badge reader": "Badge Reader",
        "door": "Door",
        "toilet": "Plumbing Fixture",
        "faucet": "Plumbing Fixture",
        "sink": "Plumbing Fixture",
        "pipe burst": "Plumbing Pipe",
        "carpet": "Flooring"
    }
    for keyword, canonical in asset_types_map.items():
        if re.search(rf'\b{keyword}\b', text_to_scan, re.I):
            asset_type = canonical
            break

    if not asset_type and re.search(r'\b(water everywhere|flooding|water leak)\b', text_to_scan, re.I):
        asset_type = "Plumbing Fixture"

    return DescriptionEntities(
        item_id=0,
        extracted_asset_type=asset_type,
        extracted_asset_id=asset_id,
        extracted_floor=floor,
        extracted_room=room
    )


# =========================================================================
# 4. Description Extraction Pipeline
# =========================================================================

def extract_description_entities(batch_size: int = 40, max_batches: Optional[int] = None) -> Dict[str, int]:
    init_entity_cache()
    print("\n[Description Extraction] Scanning distinct descriptions from bronze...")

    query = """
    SELECT 
        TRIM(b.description) AS desc_text,
        MAX(TRIM(b.building)) AS building_name
    FROM bronze.raw_tickets b
    LEFT JOIN silver.dim_description_entities c 
        ON MD5(TRIM(b.description)) = c.text_hash
    WHERE b.description IS NOT NULL 
      AND LENGTH(TRIM(b.description)) > 5
      AND c.text_hash IS NULL
    GROUP BY TRIM(b.description);
    """

    with engine.connect() as conn:
        unprocessed = [dict(r) for r in conn.execute(text(query)).mappings().all()]

    print(f"[Description Extraction] Found {len(unprocessed)} distinct uncached descriptions.")
    if not unprocessed:
        return {"regex_resolved": 0, "llm_resolved": 0}

    regex_resolved = []
    ambiguous_for_llm = []

    for item in unprocessed:
        parsed = fast_regex_extract_description(item["desc_text"], item["building_name"])
        if parsed.extracted_floor or parsed.extracted_room or parsed.extracted_asset_id or parsed.extracted_asset_type:
            regex_resolved.append((parsed, item["desc_text"]))
        else:
            ambiguous_for_llm.append(item)

    print(f"[Description Extraction] Regex Tier resolved: {len(regex_resolved)} | Ambiguous for LLM: {len(ambiguous_for_llm)}")

    insert_sql = """
    INSERT INTO silver.dim_description_entities (
        text_hash, raw_text, extracted_asset_type, extracted_asset_id, extracted_floor, extracted_room, source_method
    ) VALUES (
        :hash, :raw, :asset_type, :asset_id, :floor, :room, :method
    ) ON CONFLICT (text_hash) DO NOTHING;
    """

    # Persist Tier 1 Regex results
    with engine.begin() as conn:
        for parsed, raw_text in regex_resolved:
            conn.execute(text(insert_sql), {
                "hash": md5(raw_text),
                "raw": raw_text,
                "asset_type": parsed.extracted_asset_type,
                "asset_id": parsed.extracted_asset_id,
                "floor": parsed.extracted_floor,
                "room": parsed.extracted_room,
                "method": "REGEX"
            })

    # Tier 2: Chunked LLM Batching
    llm_resolved_count = 0
    if ambiguous_for_llm:
        FORBIDDEN_ASSETS = {
            "plumbing", "electrical", "hvac", "pest control", "access & security",
            "vertical transport", "janitorial & cleaning", "general maintenance", "it & network"
        }

        chunks = [ambiguous_for_llm[i:i + batch_size] for i in range(0, len(ambiguous_for_llm), batch_size)]
        if max_batches is not None:
            chunks = chunks[:max_batches]

        print(f"[Description Extraction] Processing {len(chunks)} LLM batch(es) ({batch_size} items per batch)...")

        llm = ChatGoogleGenerativeAI(
            model=GEMINI_MODEL,
            google_api_key=GEMINI_API_KEY,
            temperature=0.0
        ).with_structured_output(DescriptionBatchOutput)

        system_prompt = """
You are an expert NLP Entity Extractor for corporate facilities maintenance.
From each ticket description, extract:
- `extracted_asset_type`: Specific physical hardware or equipment ONLY (e.g., 'Elevator', 'Circuit Breaker', 'AC Unit', 'Sprinkler', 'Door', 'Printer', 'Toilet', 'Faucet', 'Pothole', 'Badge Reader').
  * STRICT NEGATIVE CONSTRAINT: NEVER output broad trade categories like 'Plumbing', 'Electrical', 'HVAC', or 'General Maintenance'. If no specific hardware, fixture, or equipment is named, return NULL.
- `extracted_asset_id`: Alphanumeric asset code (e.g., 'E-048', 'HP C-042', 'D-010', 'Unit E-009').
- `extracted_floor`: Specific floor number (e.g., '10', '3', '2-9'). Do NOT include the building name.
- `extracted_room`: Specific interior space or room (e.g., 'server room 391', 'room 770', 'break room', 'restroom', 'cafeteria').
  * STRICT NEGATIVE CONSTRAINT: NEVER include facility or building names (e.g., 'Tower B', 'Warehouse South', 'Remote Site Beta', 'HQ Floor 1') in the extracted_room field.
Always return the corresponding `item_id` for each item.
"""

        for batch_idx, chunk in enumerate(chunks, start=1):
            # Pass clean integer IDs: 0, 1, 2...
            batch_payload = [{"item_id": idx, "raw_text": item["desc_text"]} for idx, item in enumerate(chunk)]
            print(f"  -> Sending LLM description batch {batch_idx}/{len(chunks)} ({len(batch_payload)} records)...")

            try:
                response: DescriptionBatchOutput = llm.invoke([
                    ("system", system_prompt),
                    ("user", f"Extract entities from these descriptions:\n{json.dumps(batch_payload, indent=2)}")
                ])

                with engine.begin() as conn:
                    for item in response.items:
                        if item.item_id < 0 or item.item_id >= len(chunk):
                            continue
                        orig_row = chunk[item.item_id]
                        raw_desc = orig_row["desc_text"]
                        b_name = orig_row.get("building_name")

                        asset_clean = item.extracted_asset_type
                        if asset_clean and asset_clean.strip().lower() in FORBIDDEN_ASSETS:
                            asset_clean = None

                        room_clean = item.extracted_room
                        if room_clean and b_name:
                            room_clean = re.sub(re.escape(b_name), '', room_clean, flags=re.I).strip()
                            if not room_clean or room_clean.lower() == b_name.lower():
                                room_clean = None

                        conn.execute(text(insert_sql), {
                            "hash": md5(raw_desc),
                            "raw": raw_desc,
                            "asset_type": asset_clean,
                            "asset_id": item.extracted_asset_id,
                            "floor": item.extracted_floor,
                            "room": room_clean,
                            "method": "LLM"
                        })
                llm_resolved_count += len(response.items)
                time.sleep(0.4)
            except Exception as e:
                print(f"  [Warning] Description batch {batch_idx} failed: {e}")

    return {"regex_resolved": len(regex_resolved), "llm_resolved": llm_resolved_count}


# =========================================================================
# 5. Resolution Notes Extraction Pipeline
# =========================================================================

def extract_resolution_entities(batch_size: int = 50) -> Dict[str, int]:
    init_entity_cache()
    print("\n[Resolution Extraction] Scanning distinct resolution notes from bronze...")

    query = """
    SELECT DISTINCT TRIM(resolution_notes) AS note_text
    FROM bronze.raw_tickets
    WHERE resolution_notes IS NOT NULL
      AND LENGTH(TRIM(resolution_notes)) > 0;
    """

    with engine.connect() as conn:
        unique_notes = [r[0] for r in conn.execute(text(query)).fetchall() if r[0]]

    print(f"[Resolution Extraction] Found {len(unique_notes)} unique resolution notes.")

    regex_resolved = []
    needs_llm = []

    for note in unique_notes:
        clean_note = note.strip()

        dup_match = re.search(r'duplicate of ticket #?(\d+)', clean_note, re.I)
        if dup_match:
            regex_resolved.append({
                "raw": note,
                "is_dup": True,
                "dup_id": f"TKT-{dup_match.group(1)}",
                "action": "Closed duplicate"
            })
            continue

        if clean_note.lower() in ['fixed', 'done']:
            regex_resolved.append({
                "raw": note,
                "is_dup": False,
                "dup_id": None,
                "action": "Fixed on site"
            })
            continue

        needs_llm.append(note)

    print(f"[Resolution Extraction] Tier 1 Regex resolved: {len(regex_resolved)} | Tier 2 Routing to LLM: {len(needs_llm)}")

    insert_sql = """
    INSERT INTO silver.dim_resolution_entities (
        text_hash, raw_text, is_duplicate, duplicate_of_ticket_id, 
        root_cause, resolution_action, hours_on_site, 
        servicing_vendor_or_tech, replaced_component, source_method
    ) VALUES (
        :hash, :raw, :is_dup, :dup_id, 
        :root_cause, :action, :hours, 
        :vendor, :component, :method
    ) ON CONFLICT (text_hash) DO UPDATE SET
        is_duplicate = EXCLUDED.is_duplicate,
        duplicate_of_ticket_id = EXCLUDED.duplicate_of_ticket_id,
        root_cause = EXCLUDED.root_cause,
        resolution_action = EXCLUDED.resolution_action,
        hours_on_site = EXCLUDED.hours_on_site,
        servicing_vendor_or_tech = EXCLUDED.servicing_vendor_or_tech,
        replaced_component = EXCLUDED.replaced_component,
        source_method = EXCLUDED.source_method;
    """

    # Persist Tier 1 Regex results
    with engine.begin() as conn:
        for r in regex_resolved:
            conn.execute(text(insert_sql), {
                "hash": md5(r["raw"]),
                "raw": r["raw"],
                "is_dup": r["is_dup"],
                "dup_id": r["dup_id"],
                "root_cause": None,
                "action": r["action"],
                "hours": None,
                "vendor": None,
                "component": None,
                "method": "REGEX"
            })

    # Tier 2: LLM Batching
    llm_resolved_count = 0
    if needs_llm:
        chunks = [needs_llm[i:i + batch_size] for i in range(0, len(needs_llm), batch_size)]
        print(f"[Resolution Extraction] Calling Gemini across {len(chunks)} batch(es) for substantive notes...")

        llm = ChatGoogleGenerativeAI(
            model=GEMINI_MODEL,
            google_api_key=GEMINI_API_KEY,
            temperature=0.0
        ).with_structured_output(ResolutionBatchOutput)

        system_prompt = """
You are an expert NLP Auditor analyzing facility maintenance technician resolution notes.
For each note, extract:
- `is_duplicate`: True if note indicates duplicate ticket.
- `duplicate_of_ticket_id`: Master ticket ID if duplicate (format 'TKT-<number>').
- `root_cause`: The diagnosed cause of failure (e.g., 'faulty wiring', 'corrosion', 'power surge', 'normal wear', 'clogged filter').
- `resolution_action`: Standard action tier ('Fixed on site', 'Parts on order', 'Temporary fix', 'Could not reproduce', 'Vendor scheduled', 'Resolved by technician').
- `hours_on_site`: Float number of hours worked (e.g., from 'Took 4.8 hours' -> 4.8).
- `servicing_vendor_or_tech`: Company or crew name (e.g., 'CityWide Electric', 'PestPro Services', 'ABC Mechanical', 'overnight crew', 'Joe (in-house)').
- `replaced_component`: Specific hardware/part replaced (e.g., 'compressor', 'capacitor', 'thermostat', 'sensor', 'motor').
Always return the corresponding `item_id` for each note.
"""

        for idx, chunk in enumerate(chunks, start=1):
            batch_payload = [{"item_id": i, "raw_text": note} for i, note in enumerate(chunk)]
            print(f"  -> Sending resolution notes batch {idx}/{len(chunks)} ({len(chunk)} items)...")

            try:
                response: ResolutionBatchOutput = llm.invoke([
                    ("system", system_prompt),
                    ("user", f"Extract structured entities from these resolution notes:\n{json.dumps(batch_payload, indent=2)}")
                ])

                with engine.begin() as conn:
                    for item in response.items:
                        if item.item_id < 0 or item.item_id >= len(chunk):
                            continue
                        raw_note = chunk[item.item_id]

                        conn.execute(text(insert_sql), {
                            "hash": md5(raw_note),
                            "raw": raw_note,
                            "is_dup": item.is_duplicate,
                            "dup_id": item.duplicate_of_ticket_id,
                            "root_cause": item.root_cause,
                            "action": item.resolution_action,
                            "hours": item.hours_on_site,
                            "vendor": item.servicing_vendor_or_tech,
                            "component": item.replaced_component,
                            "method": "LLM"
                        })
                llm_resolved_count += len(response.items)
                time.sleep(0.4)
            except Exception as e:
                print(f"  [Warning] Resolution batch {idx} failed: {e}")

    print(f"[Resolution Extraction] Complete. Regex cached: {len(regex_resolved)}, LLM processed: {llm_resolved_count}.")
    return {"regex_resolved": len(regex_resolved), "llm_resolved": llm_resolved_count}