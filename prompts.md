# LLM Prompt Catalog

This document explains the prompts used by the pipeline and what each one is asked to do. It is intended to let a reviewer assess the model context, output constraints, and limits without searching through the implementation.

## How prompts are run

All active model calls use Gemini structured output with a Pydantic response model. Prompts combine stable instructions with data-derived input such as the Bronze profile, candidate rows, unique text batches, Silver column samples, or Gold staging statistics.

Structured responses pass through [`src/agents/llm_cache.py`](src/agents/llm_cache.py). The response cache key includes the provider, model, temperature, normalized prompt, and output schema. `read_write` reuses cache hits and writes successful misses to `data/llm_cache/`; `replay` requires hits and does not need a Gemini key; `refresh` calls Gemini and replaces the cached response. A changed prompt, schema, model, temperature, or input creates a new key.

### Active prompt inventory

| Stage | Agent task | Input supplied | Structured response | Temperature |
|---|---|---|---|---:|
| Silver | Data quality audit and SQL rules | Dataset catalog plus empirical Bronze profile | `ComprehensiveAuditReport` | 0.1 |
| Silver | Field swap detection | Candidate rows with category and description | `SwappedBatchOutput` | 0.0 |
| Silver | Category taxonomy mapping | Sorted unique raw category strings | `TaxonomyMappingOutput` | 0.0 |
| Silver | Description entity extraction | Batches of distinct descriptions with stable item IDs | `DescriptionBatchOutput` | 0.0 |
| Silver | Resolution note extraction | Batches of distinct resolution notes with stable item IDs | `ResolutionBatchOutput` | 0.0 |
| Gold | Schema understanding | Silver column types and sorted sample values | `SchemaSemanticUnderstanding` | 0.0 |
| Gold | Business problem design | Schema understanding plus one domain narrative | `DomainProblemsProposal` | 0.0 |
| Gold | Gold SQL generation | One problem, schema understanding, and optional audit feedback | `VerifiedSqlSolution` | 0.0 |
| Gold | Statistical audit | Row count and numeric staging statistics | `StatAuditResult` | 0.0 |

The active Gold workflow is the closed-loop graph listed above and is invoked by `run_pipeline.py`.

---

## 1. Silver Data Quality Agent

**Implementation:** `src/agents/data_quality.py`, `run_generalized_dq_agent`

**Purpose:** Compare the declared ticket data contract with measured Bronze distributions, then propose a PostgreSQL transformation for every catalog column and table-level integrity rules.

**Runtime user message:** The catalog specification and empirical profile are serialized as JSON and appended to this instruction:

```text
Here is the Dataset Catalog alongside the empirical profile from the Bronze layer. Analyze the discrepancies and generate the comprehensive audit report and SQL transformation rules:

{
  "catalog_specification": <DatasetCatalog.model_dump()>,
  "empirical_table_profile": <profile_catalog_dataset() result>
}
```

**Output contract:** Every catalog column must have exactly one `ColumnCleaningRule`; the pipeline rejects a report with missing or extra rules. Each rule includes target type, observed anomalies, severity, business risk, and a SQL expression. The output also includes table integrity rules and an executive summary.

**System prompt used:**

```text
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
     * 'crit', 'critical', 'urgent!!!', 'asap' -> 'CRITICAL'
     * 'hi', 'high' -> 'HIGH'
     * 'med', 'medium', 'normal' -> 'MEDIUM' (Map 'normal' explicitly to 'MEDIUM')
     * 'lo', 'low' -> 'LOW'
     * Missing, NULL, or '???' -> Default to 'MEDIUM'
   - `status`: Standardize lifecycle values to Title Case ('Open', 'In Progress', 'Pending Vendor', 'Escalated', 'Resolved', 'Closed'). Map corrupted sentinels ('???', 'unknown') or NULL to 'Unassigned'.
   - `building`: Map test artifacts ('???', 'unknown', 'test building') or NULLs to `NULL`.

7. CROSS-COLUMN INTEGRITY & TEMPORAL VALIDATION:
   - For `table_integrity_rules`, define validation logic for temporal anomalies where `resolved_at < created_at`.
   - Document how inverted timestamps are handled: flag them via `is_temporal_anomaly = TRUE` and set `resolved_at = NULL` in Silver to avoid corrupting duration/SLA metrics.
```

**Why these choices:** The catalog and profile provide both the intended semantics and concrete evidence. PostgreSQL-specific casting rules reduce dialect errors; deterministic IDs and sentinel handling make transformations repeatable; preserving category/description leaves semantic repair to the dedicated agent.

**Important implementation note:** The prompt asks to set `resolved_at = NULL` for inverted timestamps. Current Silver materialization preserves the parsed value and sets `is_temporal_anomaly`; the Gold SQL prompt separately requires excluding anomalous rows from duration metrics. The prompt and implementation should be reconciled before claiming this particular instruction is enforced.

---

## 2. Silver Field Swap Agent

**Implementation:** `src/agents/semantic_clean.py`, `run_semantic_swap_agent`

**Input selection:** SQL narrows the LLM workload to rows where category is longer than 25 characters and description is at most 30 characters. Results are sorted before serialization. The prompt receives candidate `ticket_id`, `raw_category`, and `raw_description` values.

**Output contract:** `SwappedBatchOutput` contains one `SwappedFieldResolution` per candidate: ticket ID, boolean decision, brief reasoning, corrected category, and corrected description.

**System prompt used:**

```text
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
```

**User message template:** `Analyze these candidate records and return swap resolutions:` followed by the JSON candidate list.

**Why these choices:** The database heuristic limits model use to likely swaps; the model verifies the relationship semantically and returns corrected values plus a reason. The swap cache is keyed by ticket ID. The heuristic can miss other swap shapes, so the candidate count should be checked against profiling and reviewed examples.

---

## 3. Silver Category Taxonomy Agent

**Implementation:** `src/agents/semantic_clean.py`, `run_semantic_taxonomy_agent`

**Input:** A sorted list of unique short raw category strings. It includes categories from Bronze and categories repaired by the swap agent. The allowed taxonomy is included in the system prompt.

**Output contract:** `TaxonomyMappingOutput` returns each raw category, one canonical category, confidence, and rationale. The expected output category must come from the fixed 11-value taxonomy.

**System prompt used:**

```text
You are a Principal Facilities Taxonomist.
You will receive a list of noisy, raw operational maintenance category strings.
Your task is to semantically classify EACH raw category into ONE of the following canonical domains:
[
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
```

**User message template:** `Categorize these unique raw category terms:` followed by a JSON array.

**Why these choices:** The closed taxonomy makes downstream aggregation consistent, and distinct-value mapping avoids one model call per ticket. Confidence and rationale make mappings auditable. The response is stored in both the prompt-response cache and the Silver taxonomy lookup table.

---

## 4. Silver Description Entity Extraction Agent

**Implementation:** `src/agents/semantic_extract.py`, `extract_description_entities`

**Input:** Distinct non-empty descriptions are sorted, handled with regex first, then unresolved descriptions are sent in batches of 40. Each item has an integer `item_id` and `raw_text`.

**Output contract:** `DescriptionBatchOutput` returns one item ID with nullable asset type, asset ID, floor, and room fields. The application checks that returned IDs are within the current batch and removes known broad trade labels and building names from extracted fields.

**System prompt used:**

```text
You are an expert NLP Entity Extractor for corporate facilities maintenance.
From each ticket description, extract:
- `extracted_asset_type`: Specific physical hardware or equipment ONLY (e.g., 'Elevator', 'Circuit Breaker', 'AC Unit', 'Sprinkler', 'Door', 'Printer', 'Toilet', 'Faucet', 'Pothole', 'Badge Reader').
  * STRICT NEGATIVE CONSTRAINT: NEVER output broad trade categories like 'Plumbing', 'Electrical', 'HVAC', or 'General Maintenance'. If no specific hardware, fixture, or equipment is named, return NULL.
- `extracted_asset_id`: Alphanumeric asset code (e.g., 'E-048', 'HP C-042', 'D-010', 'Unit E-009').
- `extracted_floor`: Specific floor number (e.g., '10', '3', '2-9'). Do NOT include the building name.
- `extracted_room`: Specific interior space or room (e.g., 'server room 391', 'room 770', 'break room', 'restroom', 'cafeteria').
  * STRICT NEGATIVE CONSTRAINT: NEVER include facility or building names (e.g., 'Tower B', 'Warehouse South', 'Remote Site Beta', 'HQ Floor 1') in the extracted_room field.
Always return the corresponding `item_id` for each item.
```

**User message template:** `Extract entities from these descriptions:` followed by a JSON array of `{item_id, raw_text}` objects.

**Why these choices:** Regex handles common structured patterns cheaply. Batching reduces request overhead; stable item IDs allow output-to-input matching; negative constraints prevent category labels or buildings from being misclassified as physical assets or rooms.

---

## 5. Silver Resolution Note Extraction Agent

**Implementation:** `src/agents/semantic_extract.py`, `extract_resolution_entities`

**Input:** Distinct non-empty resolution notes, sorted and sent in batches of 50. Exact duplicate references and the notes `fixed`/`done` are handled by deterministic regex rules before the LLM stage. Each LLM item has an `item_id` and `raw_text`.

**Output contract:** `ResolutionBatchOutput` returns duplicate status/reference, root cause, a standardized resolution action, on-site hours, vendor/technician, and replaced component for each item ID.

**System prompt used:**

```text
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
```

**User message template:** `Extract structured entities from these resolution notes:` followed by a JSON array of `{item_id, raw_text}` objects.

**Why these choices:** The output separates duplicate references from actionable repair details and turns notes into queryable measures. Deterministic fast paths handle simple cases without model calls.

---

## 6. Gold Schema Understanding Agent

**Implementation:** `src/agents/gold_closed_loop_agent.py`, `agent_understand_schema`

**Input:** Silver column names, PostgreSQL data types, and up to five distinct values per column. Samples are sorted; the volatile `_silver_processed_at` column is excluded so a new processing timestamp does not invalidate an otherwise identical cache key.

**Prompt used:**

```text
You are a Principal Data Warehouse Architect in Corporate Facilities Management.
Examine this empirical schema profile from our PostgreSQL table (`silver.cleaned_tickets`), including each column's data type and top distinct sample values:
<JSON column_context>

For each column:
1. Retain the `sample_values` as provided.
2. Explain what it represents in physical facility operations.
3. Identify analytical pitfalls or quality traps (e.g., temporal inversions, duplicates, nulls).
```

**Output contract:** `SchemaSemanticUnderstanding` provides an executive interpretation plus per-column business meaning and analytic pitfalls.

**Why these choices:** Empirical samples ground later Gold SQL in real database values and casing rather than assumptions about the data.

---

## 7. Gold Business Problem Design Agent

**Implementation:** `src/agents/gold_closed_loop_agent.py`, `agent_formulate_domain_problems`

**Input:** The cached schema understanding, the domain name, and the domain narrative from `run_pipeline.py`.

**Prompt used:**

```text
You are a Principal Enterprise Data Warehouse Architect and Strategic Operations Consultant.
Analyze the Silver schema semantics:
<SchemaSemanticUnderstanding JSON>

Business Domain: "<domain_name>"
Context Narrative:
"""<business_domain_narrative>"""

For this domain, formulate EXACTLY 2 distinct, high-impact business problem statements at the annual grain (`incident_year`).
For EACH problem statement, provide thorough analytical depth:
1. `why_this_problem_matters`: Explain the deep operational "why behind", including the financial or business risk of not tracking this.
2. `key_stakeholders`: Provide specific profiles for 2-3 roles (their title, core mandate, and current blind spot).
3. `how_model_solves_problem`: Explain how this specific Gold model (its grain, dimensions, semantic filters, and metrics) solves that blind spot.
4. `decisions_enabled`: List concrete operational decisions facilities leadership can make.
5. `target_table_name`: Valid lowercase snake_case prefixed with 'gold.'.
```

**Output contract:** `DomainProblemsProposal` contains exactly two problem statements per domain, with stakeholder context, grain, dimensions, metrics, table name, and decisions enabled.

**Why these choices:** It asks for the business rationale before the SQL, so model design is tied to a stakeholder decision rather than just aggregation convenience.

**Scope note:** `run_pipeline.py` supplies two domains, and each prompt asks for two problems, so the active workflow proposes four Gold models in total.

---

## 8. Gold SQL Generation and Self-Verification Agent

**Implementation:** `src/agents/gold_closed_loop_agent.py`, `agent_generate_and_self_verify_sql`

**Input:** One problem statement, the schema understanding, and feedback from the prior statistical audit when a retry is needed.

**Prompt used:**

```text
You are an expert PostgreSQL 16 Analytics Engineer.
Generate the analytical SELECT query to solve this business problem:
<GoldProblemStatement JSON>

Column Semantics, Types, & Real Distinct Sample Values:
<SchemaSemanticUnderstanding JSON>

<If a prior audit failed: PREVIOUS RUN FAILED WITH THESE METRIC VIOLATIONS: <feedback>. FIX THIS IN THE SQL!>

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
```

**Output contract:** `VerifiedSqlSolution` contains the target name, a verification rationale, and one SELECT query. The graph creates a staging table, measures results, asks the audit agent to review them, and retries with feedback when needed.

**Why these choices:** The prompt carries empirical values and explicit metric rules into SQL generation. Returning only a SELECT keeps staging and promotion under pipeline control.

**Limit:** The result is still model-authored SQL. Prompt constraints reduce mistakes but are not a SQL parser or allowlist; staging and metric auditing are the runtime checks.

---

## 9. Gold Statistical Audit Agent

**Implementation:** `src/agents/gold_closed_loop_agent.py`, `agent_audit_gold_stats`

**Input:** The generated staging table's row count and numeric-column minimum, maximum, average, negative count, and null count.

**Prompt used:**

```text
You are an expert Data Quality Auditor reviewing the statistical output of a newly computed Gold data mart.
Inspect the following column statistics extracted from PostgreSQL:
<JSON stats_payload>

EVALUATION RULES:
1. Any duration/resolution time (e.g. `avg_resolution_time_hours`) CANNOT be negative. If `min < 0`, that is an automatic failure!
2. Any cost metric (e.g. `total_cost_usd`, `avg_cost_per_incident_usd`) CANNOT be negative.
3. Row count cannot be 0.
4. Net actionable incidents cannot be greater than gross incidents.

Return whether the dataset is statistically sound. If violations exist, specify exact details and SQL fix.
```

**Output contract:** `StatAuditResult` returns a sound/unsound decision, detected violations, and an optional suggested SQL patch.

**Why these choices:** It checks simple physical and business invariants after execution, when actual output statistics are available. It does not establish semantic correctness of every metric.

---

