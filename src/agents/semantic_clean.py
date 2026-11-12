import json
from typing import List, Dict, Optional
from pydantic import BaseModel, Field
from sqlalchemy import text
from src.agents.llm_cache import invoke_gemini_structured
from src.db import silver_engine as engine

# -------------------------------------------------------------------------
# 1. Structured Output Schemas (Pydantic v2)
# -------------------------------------------------------------------------

CANONICAL_TAXONOMY = [
    "Electrical",
    "HVAC",
    "Plumbing",
    "Fire & Safety",
    "Pest Control",
    "Access & Security",
    "Vertical Transport",
    "Janitorial & Cleaning",
    "IT & Network",
    "General Maintenance",
    "Unassigned"
]


class SwappedFieldResolution(BaseModel):
    ticket_id: str
    is_swapped: bool = Field(
        description="True if raw_category is a descriptive sentence and raw_description is a short category tag."
    )
    semantic_reasoning: str = Field(
        description="Brief explanation of why the agent considers this swapped or normal."
    )
    clean_category: str = Field(
        description="The short category label (either raw_category or rescued from raw_description)."
    )
    clean_description: str = Field(
        description="The full incident description (either raw_description or rescued from raw_category)."
    )


class SwappedBatchOutput(BaseModel):
    resolutions: List[SwappedFieldResolution]


class CategoryMappingItem(BaseModel):
    raw_category: str
    canonical_category: str = Field(
        description=f"Must be one of: {', '.join(CANONICAL_TAXONOMY)}"
    )
    confidence: float = Field(
        description="Confidence score between 0.0 and 1.0"
    )
    rationale: str = Field(
        description="Semantic rationale (e.g. 'cold refers to air conditioning/heating issue -> HVAC')"
    )


class TaxonomyMappingOutput(BaseModel):
    mappings: List[CategoryMappingItem]


# -------------------------------------------------------------------------
# 2. Database DDL for Persistence / Caching
# -------------------------------------------------------------------------

SWAP_LOOKUP_DDL = """
CREATE TABLE IF NOT EXISTS silver.dim_field_swap_cache (
    ticket_id VARCHAR(50) PRIMARY KEY,
    is_swapped BOOLEAN NOT NULL DEFAULT FALSE,
    clean_category TEXT NOT NULL,
    clean_description TEXT NOT NULL,
    semantic_reasoning TEXT,
    resolved_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS silver.dim_category_taxonomy (
    raw_category TEXT PRIMARY KEY,
    canonical_category VARCHAR(50) NOT NULL,
    confidence NUMERIC(3,2),
    rationale TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
"""

def init_semantic_tables():
    """Initializes lookup cache tables in PostgreSQL."""
    with engine.begin() as conn:
        conn.execute(text(SWAP_LOOKUP_DDL))


# -------------------------------------------------------------------------
# 3. Step 1: Semantic Column-Swap Detection Agent
# -------------------------------------------------------------------------

def resolve_field_swap_candidates(candidates: List[dict]) -> List[SwappedFieldResolution]:
    """Run the production swap prompt against supplied candidate rows."""
    if not candidates:
        return []

    system_prompt = """
You are a Semantic Data Cleansing Agent specialized in operational support tickets.
You will inspect a batch of operational maintenance tickets where `raw_category` and `raw_description` appear inverted.

For each ticket:
1. Examine the natural language semantics:
   - Does `raw_category` contain a full descriptive sentence (e.g. "Wasp nest forming on HQ Floor 3 exterior", "Security camera offline", "window cracked")?
   - Does `raw_description` contain a short discipline or trade code (e.g. "Pest", "SECURITY", "AC", "CLEANING", "Elec")?
2. If YES:
   - Mark `is_swapped = True`
   - Set `clean_category = raw_description`
   - Set `clean_description = raw_category`
3. If NO:
   - Mark `is_swapped = False`
   - Retain original assignments
"""
    user_prompt = f"Analyze these candidate records and return swap resolutions:\n{json.dumps(candidates, indent=2)}"
    messages = [("system", system_prompt), ("user", user_prompt)]
    response: SwappedBatchOutput = invoke_gemini_structured(
        SwappedBatchOutput, messages, temperature=0.0
    )
    return response.resolutions


def run_semantic_swap_agent() -> List[SwappedFieldResolution]:
    """
    Identifies candidate records where columns may be inverted,
    and uses Gemini to semantically verify and swap them back.
    """
    init_semantic_tables()
    print("\n[Semantic Agent: Step 1] Extracting swap candidate pairs from bronze...")

    # Statistical filter: category has sentence length (>25), description is short (<=30)
    query = """
    SELECT 
        COALESCE(ticket_id, 'SYNTH-' || SUBSTRING(_row_hash, 1, 8)) AS ticket_id,
        category AS raw_category,
        description AS raw_description
    FROM bronze.raw_tickets
    WHERE LENGTH(TRIM(COALESCE(category, ''))) > 25 
      AND LENGTH(TRIM(COALESCE(description, ''))) <= 30
    ORDER BY ticket_id, raw_category, raw_description;
    """

    with engine.connect() as conn:
        candidates = [dict(r) for r in conn.execute(text(query)).mappings().all()]

    print(f"[Semantic Agent: Step 1] Found {len(candidates)} candidate swapped records.")
    if not candidates:
        return []

    print("[Semantic Agent: Step 1] Sending candidate batch to Gemini for semantic resolution...")
    resolutions = resolve_field_swap_candidates(candidates)

    # Persist to database cache
    upsert_sql = """
    INSERT INTO silver.dim_field_swap_cache (
        ticket_id, is_swapped, clean_category, clean_description, semantic_reasoning
    ) VALUES (
        :ticket_id, :is_swapped, :clean_category, :clean_description, :semantic_reasoning
    ) ON CONFLICT (ticket_id) DO UPDATE SET
        is_swapped = EXCLUDED.is_swapped,
        clean_category = EXCLUDED.clean_category,
        clean_description = EXCLUDED.clean_description,
        semantic_reasoning = EXCLUDED.semantic_reasoning;
    """

    with engine.begin() as conn:
        for res in resolutions:
            conn.execute(text(upsert_sql), {
                "ticket_id": res.ticket_id,
                "is_swapped": res.is_swapped,
                "clean_category": res.clean_category,
                "clean_description": res.clean_description,
                "semantic_reasoning": res.semantic_reasoning
            })

    print(f"[Semantic Agent: Step 1] Persisted {len(resolutions)} verified swap records into silver.dim_field_swap_cache.")
    return resolutions


# -------------------------------------------------------------------------
# 4. Step 2: Semantic Taxonomy Normalization Agent
# -------------------------------------------------------------------------

def classify_categories(raw_categories: List[str]) -> List[CategoryMappingItem]:
    """Run the production taxonomy prompt against supplied category strings."""
    if not raw_categories:
        return []

    system_prompt = f"""
You are a Principal Facilities Taxonomist.
You will receive a list of noisy, raw operational maintenance category strings.
Your task is to semantically classify EACH raw category into ONE of the following canonical domains:
{json.dumps(CANONICAL_TAXONOMY, indent=2)}

Semantic Guidelines:
- Slang/Symptom words:
  * 'cold', 'air conditioning', 'heating cooling', 'Climate Control' -> 'HVAC'
  * 'pipe burst', 'water issue', 'Water/Plumbing' -> 'Plumbing'
  * 'power issue', 'Power', 'Elec', 'ELECTRICAL' -> 'Electrical'
  * 'WiFi', 'Connectivity', 'Network', 'IT Support' -> 'IT & Network'
  * 'Sprinkler', 'Fire Alarm', 'fire safety' -> 'Fire & Safety'
  * 'Badge/Access', 'Access Control', 'security' -> 'Access & Security'
  * 'Lift', 'ELEVATOR', 'elevator/escalator' -> 'Vertical Transport'
  * 'janitorial', 'Housekeeping', 'CLEANING' -> 'Janitorial & Cleaning'
- Noise, test placeholders, or invalid text:
  * 'DELETE ME', 'asdf', 'test', '???', 'unknown', 'Misc', 'Other', 'General' -> 'Unassigned'
"""
    user_prompt = f"Categorize these unique raw category terms:\n{json.dumps(raw_categories, indent=2)}"
    messages = [("system", system_prompt), ("user", user_prompt)]
    response: TaxonomyMappingOutput = invoke_gemini_structured(
        TaxonomyMappingOutput, messages, temperature=0.0
    )
    return response.mappings


def run_semantic_taxonomy_agent() -> List[CategoryMappingItem]:
    """
    Extracts all distinct valid category labels across the dataset,
    and uses Gemini to map them into the canonical 11-discipline taxonomy.
    """
    init_semantic_tables()
    print("\n[Semantic Agent: Step 2] Extracting unique categories for canonical mapping...")

    # Extract all distinct category names that are true category labels
    # (Excluding the long swapped sentence categories which are now swapped into descriptions)
    query = """
    SELECT DISTINCT TRIM(category) AS raw_cat
    FROM bronze.raw_tickets
    WHERE category IS NOT NULL 
      AND LENGTH(TRIM(category)) <= 35
    UNION
    SELECT DISTINCT clean_category AS raw_cat
    FROM silver.dim_field_swap_cache
    ORDER BY raw_cat;
    """

    with engine.connect() as conn:
        raw_categories = [r[0] for r in conn.execute(text(query)).fetchall() if r[0]]

    print(f"[Semantic Agent: Step 2] Extracted {len(raw_categories)} unique raw category tags.")

    print("[Semantic Agent: Step 2] Calling Gemini to classify distinct categories in batch...")
    mappings = classify_categories(raw_categories)

    # Persist mapping table
    upsert_sql = """
    INSERT INTO silver.dim_category_taxonomy (
        raw_category, canonical_category, confidence, rationale
    ) VALUES (
        :raw_category, :canonical_category, :confidence, :rationale
    ) ON CONFLICT (raw_category) DO UPDATE SET
        canonical_category = EXCLUDED.canonical_category,
        confidence = EXCLUDED.confidence,
        rationale = EXCLUDED.rationale;
    """

    with engine.begin() as conn:
        for m in mappings:
            conn.execute(text(upsert_sql), {
                "raw_category": m.raw_category,
                "canonical_category": m.canonical_category,
                "confidence": m.confidence,
                "rationale": m.rationale
            })

    print(f"[Semantic Agent: Step 2] Successfully persisted {len(mappings)} taxonomy mappings into silver.dim_category_taxonomy.")
    return mappings
