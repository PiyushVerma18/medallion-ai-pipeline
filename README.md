# Autonomous Medallion AI Pipeline: Operational Facilities Intelligence

An enterprise-grade Medallion Data Lakehouse (Bronze → Silver → Gold) architecture orchestrated with LangGraph, PostgreSQL 16, and Gemini 2.5 Flash. The pipeline transforms ~10,280 messy, heterogeneous operational facility support tickets into production-grade analytical data marts using autonomous, self-healing AI agents.

## Table of Contents

- Architecture Overview

- Medallion Layer Design & Cleaning Rules

- Gold Analytical Models & Business Value

- Agent Assessment (Honesty Over Impressiveness)

- Trust vs. Override Framework

- What Changes at 100x Scale (1M+ to 10M+ Rows)

- Observability & Evaluation Harness

- Quickstart: How to Run

## 1. Architecture Overview

![Alt text](assets/Structural_diagram.png)

## 2. Medallion Layer Design & Cleaning Rules

The pipeline enforces strict separation of concerns across PostgreSQL schemas: bronze, silver, and gold.

### Bronze Layer (bronze.raw_tickets)

- Philosophy: Schema-on-read ingestion with zero data loss. All incoming columns are stored as raw text (TEXT) to preserve input fidelity.
- Lineage Tracking:
1)  _row_hash: MD5 digest across all raw row values, serving as the immutable primary key for idempotent upserts.
2) _source_file: Name of the source file (e.g., raw_tickets.csv).
3) _ingested_at: UTC timestamp recording when the record entered the data warehouse.

### Silver Layer (silver.cleaned_tickets)

- Philosophy: Validated, typed, deduplicated, and semantically enriched table providing a single source of truth.
- Cleaning Rules & Rationale:

| Target Column | Raw Data Issue | Cleansing & Transformation Rule | Operational Rationale |
| :--- | :--- | :--- | :--- |
| `ticket_id` | Missing values, malformed strings, or duplicates. | Synthesize IDs for malformed entries (`TKT-SYNTH-<hash8>`). Deduplicate using `ROW_NUMBER() OVER (PARTITION BY clean_ticket_id ORDER BY _ingested_at DESC)`. | Guarantees primary key uniqueness without dropping rows containing valid operational notes. |
| `created_at` / `resolved_at` | 7 mixed formats (Unix epoch, ISO 8601, `YYYY-MM-DD`, `MM/DD/YYYY HH12:MI AM`, `DD-Mon-YYYY`, string sentinels like `TBD`, `never`, `00/00/0000`). | Regex-driven `CASE` statement mapping valid formats into standard `TIMESTAMP`. String sentinels map to `NULL`. | Normalizes disparate timestamp logging conventions across legacy CMMS tools into queryable ISO timestamps. |
| `is_temporal_anomaly` | 1,866 tickets contain inverted timestamps where `resolved_at < created_at` (e.g., created 2025, resolved 2024). | Non-destructive audit flag: `CASE WHEN resolved_at < created_at THEN TRUE ELSE FALSE END`. | Prevents inverted timestamps from corrupting resolution time and MTTR metrics without deleting records. |
| `is_field_swapped` | 46 records have `category` and `description` swapped (e.g., `category` contains a 200-character description). | Heuristic filter (`LENGTH(category) > 40 AND LENGTH(description) < 25`) verified by Gemini, cached in `silver.dim_field_swap_cache`. | Realigns inverted columns so categorization and description fields are correctly populated. |
| `canonical_category` | 113 noisy, colloquial, or misspelled categories (e.g., `ac`, `hvac leak`, `Pest`, `bug extermination`). | Clustered and mapped into an 11-discipline canonical taxonomy via Agent 2, cached in `silver.dim_category_taxonomy`. | Enables cross-campus spend aggregation across standardized trade disciplines. |
| `priority` | Informal entries (`crit`, `urgent!!!`, `asap`, `hi`, `med`, `lo`). | Normalized into an uppercase standard enum: `'CRITICAL'`, `'HIGH'`, `'MEDIUM'`, `'LOW'`. | Restores queryable cardinality for SLA dispatch and triage tracking. |
| `status` | Case variations and typos (`open`, `in progress`, `pending vendor`, `escalated`, `resolved`, `closed`). | Canonicalized to Title Case. Invalid or empty statuses default to `'Unassigned'`. | Standardizes ticket lifecycle tracking for operational backlog reporting. |
| `cost` | Negative values (`-1`, `-999`), currency symbols (`$`, `,`), and sentinels (`TBD`, `error`). | Strips currency characters. Validates numeric range: `CASE WHEN cost::NUMERIC < 0 THEN NULL ELSE cost::NUMERIC(10,2) END`. | Eliminates invalid sentinels while preserving genuine financial transactions. |
| `sla_hours` | Invalid limits (`999`, `-1`, `0`). | Sets `<= 0` or `>= 999` to `NULL`. | Prevents artificial SLA breaches or infinite compliance margins. |
| `is_duplicate` / `duplicate_of_ticket_id` | Call-center duplicate complaints for the same physical issue. | Extracted from resolution notes (e.g., `"Duplicate of TKT-1234"`, `"Already reported"`). | Separates gross call-center noise from net actionable operational workload. |
| Entity Enrichments | Unstructured descriptions and resolution notes. | Extracted into structured columns: `extracted_asset_type`, `extracted_asset_id`, `extracted_floor`, `extracted_room`, `root_cause`, `resolution_action`, `hours_on_site`. | Transforms free text into dimensional attributes for hardware failure and technician labor analysis. |

## 3. Gold Analytical Models & Business Value

The Gold Layer is designed around an annual grain (incident_year). In our dataset, 99.7% of tickets occur across 2024 (6,834) and 2025 (3,414). Grouping by month would fragment 17 buildings and 11 categories into over 3,300 sparse groups (~3 tickets/cell). An annual grain aligns with enterprise CapEx/OpEx budget planning and vendor Master Service Agreement (MSA) review cycles.

### Domain 1: Corporate Real Estate & Facilities Operations (CRE / FM)

Target Stakeholders: VP of Real Estate, Corporate Financial Controller, Facilities Operations Director.

### Model 1: gold.annual_facilities_opex_summary (Materialized View — 342 rows)

- Grain: incident_year × building × canonical_category

- Core Metrics: total_annual_cost (min: $13.2k, max: $263.8k, avg: $106.9k), total_annual_hours_on_site, total_annual_tickets.

- Business Justification: Uncovers trade disciplines driving disproportionate spend per campus (e.g., HVAC at Remote Site Beta requiring $345k/yr) to guide annual budget reallocations and target maintenance interventions.

### Model 2: gold.annual_maintenance_efficiency_and_risk_summary (Materialized View — 1,179 rows)

- Grain: incident_year × building × canonical_category × extracted_asset_type

- Core Metrics: total_annual_unique_tickets, total_annual_duplicate_tickets, total_annual_critical_tickets, total_annual_critical_cost.

- Business Justification: Isolates call-center noise (duplicate rates reaching up to 40% in high-traffic cohorts) from true infrastructure risk (priority = 'CRITICAL'), providing real estate leadership with clean data to justify capital expenditure (CapEx) for plant overhauls.

### Domain 2: Asset Reliability & Sourcing Performance (Maintenance Engineering)

Target Stakeholders: Chief Reliability Engineer, Maintenance Supervisor, Procurement Lead.

### Model 3: gold.annual_asset_failure_analysis (Materialized View — 2,334 rows)

- Grain: incident_year × building × extracted_asset_type × extracted_asset_id × root_cause

- Core Metrics: total_incidents, avg_resolution_time_hours (min: 34.3 hrs, avg: 4,962 hrs), total_cost, total_hours_on_site.

- Business Justification: Pinpoints chronic "bad-actor" equipment (such as specific elevator units or HVAC compressors) and links failures to underlying causes (e.g., corrosion, clogged filters, faulty wiring) to transition from reactive repairs to targeted preventative maintenance schedules.

### Model 4: gold.annual_maintenance_workforce_performance (Materialized View — 373 rows)

- Grain: incident_year × building × canonical_category × workforce_type (Internal vs. Vendor)

- Core Metrics: total_incidents, avg_resolution_time_hours (min: 53.6 hrs, avg: 4,648 hrs), total_labor_hours, total_costs.

- Business Justification: Evaluates third-party contractors against in-house technicians across comparable trade disciplines and building tiers, providing objective data for annual vendor contract renewals and SLA renegotiations.

## 4. Agent Assessment (Honesty Over Impressiveness)

| Agent | Core Function | Sample Input / Output | Honest Assessment |
| :--- | :--- | :--- | :--- |
| **Agent 1: Data Quality & Profiling Agent** | Profiles Bronze distributions, identifies dirty timestamp formats, sentinels, and anomalies; generates SQL cleaning logic with business impact rationales. | **Input**: Profile showing cost strings (`-1`, `TBD`, `$1,200`) and 7 timestamp patterns.<br><br>**Output**: Synthesized `CASE` regex expressions handling non-destructive casts and `is_temporal_anomaly` flags. | **Saved**: 2+ hours manually cataloging mixed date strings and edge-case cost formats.<br><br>**Trouble**: Initially proposed non-standard SQL dialect expressions (`TRY_CAST`, not available in standard PostgreSQL). Fixed by constraining the prompt to standard PostgreSQL 16 `CASE` expressions. |
| **Agent 2: Semantic Classification & Entity Extraction Agent** | Detects field-swapped records, normalizes 113 colloquial categories into an 11-discipline taxonomy, and extracts structured entities from free-text notes. | **Input**: `"water everywhere in bathroom Remote Site Beta"`<br><br>**Output**: `canonical_category = 'Plumbing'`, `extracted_asset_type = 'Plumbing Fixture'`, `extracted_room = 'bathroom'`. | **Saved**: Extracted operational fields (`asset_id`, `root_cause`, `action`, `hours_on_site`) that static regex alone could not reliably isolate.<br><br>**Trouble**: Row-by-row LLM inference over 10,280 rows was too slow and costly. We built a hybrid approach: regex fast-paths, distinct string deduplication (74 categories, 901 unique resolution notes), and DB table caching, cutting LLM calls from >10,000 to <25 batched requests. |
| **Agent 3: Closed-Loop Gold Layer Design Agent** | Autonomously formulates business problem statements, drafts dimensional SQL queries, profiles staging data, and self-heals metric violations. | **Input**: Silver schema + domain narrative.<br><br>**Output**: Production-ready materialized views with verified non-negative durations and executive problem dossiers. | **Saved**: Handled schema design, grain selection, and boilerplate SQL generation, delivering business dossiers alongside working code.<br><br>**Trouble**: Suffered two notable pitfalls on initial runs: negative resolution times due to unfiltered temporal anomalies (−11,602 hrs), and empty tables from case-sensitive string matching (`priority IN ('High', 'Critical')`). Resolved via Empirical Data Grounding and the Closed-Loop Feedback Gate. |

## 5. Trust vs. Override Framework

Autonomous agents accelerate pipeline design, but human engineering oversight is necessary to catch edge cases that violate domain logic:

| When to Trust the Agent | When to Override the Agent |
| :--- | :--- |
| **1. Dimensional Modeling Scaffolding:**<br>Star schema design, grain selection, dimension vs. fact separation, and join keys. | **1. Temporal Lifecycle Integrity:**<br>The LLM may write `AVG(resolved_at - created_at)` without filtering `is_temporal_anomaly = FALSE` or tickets still `In Progress`, yielding negatives. |
| **2. Dialect-Specific SQL Authoring:**<br>Writing clean PostgreSQL 16 CTEs, window functions, and conditional `FILTER (WHERE ...)` clauses. | **2. Business Metric Denominators:**<br>When calculating average cost per incident, the LLM may divide by gross tickets instead of net actionable tickets (`WHERE NOT is_duplicate`). |
| **3. Multi-Persona Metric Ideation:**<br>Brainstorming multi-faceted operational KPIs across finance, reliability, and workforce personas. | **3. Missing Data vs. Zero Sentinels:**<br>The LLM may treat null costs as $0.00, skewing averages unless guided to filter null sentinels. |
| **4. Unstructured Text Entity Normalization:**<br>Parsing descriptive strings into structured labels (e.g., mapping colloquial terms to standard names). | **4. Contractual & SLA Specifics:**<br>The LLM cannot infer custom SLA schedules (e.g., business hours vs. calendar hours) without context. |

## 6. What Changes at 100x Scale (10K+ to 10M+ Rows)

If this pipeline were scaled from 10k+ rows to 10M+ daily records:

| Architectural Dimension | Current Architecture (10k Rows) | 100x Production Redesign (1M+ to 10M+ Rows) |
| :--- | :--- | :--- |
| **Compute & Storage Engine** | Single-instance PostgreSQL 16 on local Docker volume. | Decoupled Lakehouse storage using **Apache Iceberg / Delta Lake** on Amazon S3, queried via **DuckDB** (micro-batch) or **Apache Spark / Trino** (distributed). |
| **LLM Inference Strategy** | Chunked synchronous API calls with local DB caching. | • **Asynchronous Batch APIs** (e.g., Gemini Batch) running overnight at 50% lower cost.<br>• **Semantic Clustering**: Generate embeddings with `pgvector`/Qdrant; cluster 10M records into ~1,000 centroids; run the LLM only on centroids and propagate labels via vector similarity. |
| **Ingestion Pipeline** | Batch CSV ingestion into temporary staging tables. | Change Data Capture (CDC) with **Kafka** and **Debezium**, appending micro-batches with watermark tracking on `_ingested_at`. |
| **Gold Transformations** | Full materialized view creation (`CREATE MATERIALIZED VIEW AS`). | Incremental **dbt models** (`is_incremental()`) partitioned by `incident_year` and `incident_month`, refreshing only active time windows. |
| **Data Quality Profiling** | Exact table scans (`COUNT(*)`, `COUNT(DISTINCT)`). | Approximate profiling using **HyperLogLog** (cardinality) and **t-digest** (quantiles) to prevent out-of-memory errors on large scans. |



## 7. Observability & Evaluation Harness

The repository includes a dedicated test harness (tests/test_pipeline_invariants.py) that acts as an automated regression gate:

``` python -m pytest tests/test_pipeline_invariants.py ```

### Invariant Checks Verified:

1) Bronze Lineage Completeness: Asserts 100% of rows carry valid _source_file, _ingested_at, and _row_hash identifiers.

2) Silver Deduplication & Swap Ground Truth: Validates that all 46 swapped records are resolved, 823 duplicates are marked, and 1,866 temporal anomalies are properly flagged.

3) Gold Non-Negativity Invariant: Scans every numeric column across all 4 production Gold marts to verify that no negative durations or invalid costs reach end-user tables (MIN(col) >= 0).

## 8. Quickstart: How to Run

### Prerequisites

- Docker and Docker Compose

- Python 3.9+

- Google Gemini API Key

### Step-by-Step Setup

1) Clone Repository & Configure Environment:

```
git clone https://github.com/PiyushVerma18/medallion-ai-pipeline
cd medallion-ai-pipeline
cp .env.example .env 
```
Add your API key inside .env:

```
DATABASE_URL=postgresql://pipeline_user:pipeline_password@localhost:5432/ticket_warehouse
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash
```

2) Start PostgreSQL Container:

``` docker-compose up -d ```

3) Install Dependencies in Virtual Environment:

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

4) Run the Full Pipeline:

```python run_pipeline.py```

This single command executes Bronze ingestion, Silver cleaning and enrichment, and the Gold closed-loop agent, printing the final observability scorecard:

```
==========================================================================================
📊 END-TO-END PIPELINE OBSERVABILITY & SLA SCORECARD
==========================================================================================
Total Execution Runtime     : 250.18 seconds
Bronze Raw Records Ingested : 10,280
Silver Validated Records    : 10,280
  • Inverted Fields Repaired: 46
  • Duplicate Tickets Marked: 823
  • Temporal Anomalies Flagged: 1,866
------------------------------------------------------------------------------------------
PRODUCTION GOLD DATA MARTS:
  ✔ Model 1: gold.annual_facilities_opex_summary             | Rows:   342 | Domain: Corporate Real Estate & Faci...
  ✔ Model 2: gold.annual_maintenance_efficiency_and_risk_summary | Rows: 1,179 | Domain: Corporate Real Estate & Faci...
  ✔ Model 3: gold.annual_asset_failure_analysis              | Rows: 2,334 | Domain: Asset Reliability & Sourcing...
  ✔ Model 4: gold.annual_maintenance_workforce_performance   | Rows:   373 | Domain: Asset Reliability & Sourcing...
==========================================================================================
```

5) Run Data Invariant Tests:

``` python -m pytest tests/test_pipeline_invariants.py ```