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

The Gold Layer is modeled around an annual grain (incident_year), reflecting enterprise fiscal planning and vendor Master Service Agreement (MSA) review cycles. In the underlying operational data, ticket volume is concentrated in 2024 (6,490 tickets across 170 cohorts) and 2025 (3,266 tickets across 170 cohorts), alongside 2 late-2023 tickets, totaling 9,758 actionable incidents across 17 facilities and 10 canonical trade disciplines. Grouping by month would fragment the data into sparse cohorts (~3 tickets per cell), whereas an annual grain maintains statistical significance for CapEx, OpEx, and reliability decisions.

### Domain 1: Corporate Real Estate & Facilities Operations (CRE / FM)

### Model 1: gold.annual_facilities_opex_summary

- Table Type: Materialized View
- Dataset Shape: 342 rows × 6 columns
- Dimensional Grain: incident_year × building × canonical_category
- Schema Definition:
1) incident_year (INT): Operational year (2023, 2024, 2025).
2) building (TEXT): Campus location (17 distinct facilities, e.g., Main Office, Tower A, Remote Site Alpha).
3) canonical_category (VARCHAR): Normalized maintenance discipline (10 canonical classes, e.g., HVAC, Electrical, Plumbing, Vertical Transport).
4) total_annual_cost (NUMERIC): Total annual operational expenditure per building-discipline cohort.
5) total_annual_hours_on_site (NUMERIC): Cumulative field labor hours logged on site.
6) total_annual_tickets (BIGINT): Annual incident volume.

### The "Why Behind" This Problem

Without clear visibility into annual OPEX by building and maintenance category, the organization cannot identify cost drivers, negotiate better vendor contracts, or optimize internal resource deployment. This leads to budget overruns, inefficient spending, and a reactive rather than proactive approach to facilities management. The financial risk is significant, as unmanaged OPEX directly impacts profitability and diverts funds from strategic initiatives while preventing accurate forecasting and capital planning.

### Key Stakeholders & Pain Points

- VP of Corporate Real Estate (Portfolio Optimization & CapEx Planning): Lacks a consolidated, granular view of annual OPEX by building and maintenance category, making it impossible to identify high-cost facilities or underperforming trade disciplines, leading to budget surprises and reactive cost-cutting.

- Facilities Operations Director (Day-to-day Operations & Budget Adherence): Struggles to justify resource allocation or budget requests without clear data on where maintenance costs are concentrated annually, making it difficult to demonstrate ROI for preventative maintenance or identify areas for process improvement.

- Corporate Financial Controller (Financial Reporting & Cost Control): Receives aggregated, high-level facilities expenditure reports that lack the necessary detail to challenge spending patterns or identify specific areas of financial leakage within the facilities domain.

### How This Model Solves It

At an annual grain (EXTRACT(YEAR FROM created_at)::INT), this model aggregates cost and hours_on_site grouped by building and canonical_category. It provides a standardized view of annual operational expenditures and labor hours, allowing stakeholders to pinpoint exactly which facilities and maintenance categories drive peak resource consumption without manual ticket inspection.

### Empirical Data Grounding & Statistical Profiling

- Total Portfolio OPEX: $36,462,034.63 across 341 non-null cohorts (2024: $24.47M across 6,490 tickets; 2025: $11.98M across 3,266 tickets; 2023: $13.53k across 2 tickets).

- total_annual_cost: Min = $13,226.20, Max = $263,834.22, Mean = $106,926.79 per cohort (0 negative values).

- total_annual_hours_on_site: Min = 0.60 hrs, Max = 42.10 hrs, Mean = 10.95 hrs (3,075.80 total on-site labor hours logged).

- total_annual_tickets: Min = 1, Max = 60, Mean = 28.53 tickets per cohort.

### Strategic Decisions Enabled

- Reallocate annual facilities budgets based on historical trade demand per campus facility.

- Identify high-cost buildings requiring targeted preventative maintenance or CapEx overhauls.

- Negotiate volume-discounted vendor contracts for categories with heavy annual financial commitments.

- Benchmark labor intensity (total_annual_hours_on_site) against material expenditure per trade discipline.

### Model 2: gold.annual_maintenance_efficiency_and_risk_summary

- Table Type: Materialized View

- Dataset Shape: 1,179 rows × 8 columns

- Dimensional Grain: incident_year × building × canonical_category × extracted_asset_type

- Schema Definition:

1) incident_year (INT): Operational calendar year.

2) building (TEXT): Campus facility.

3) canonical_category (VARCHAR): Trade discipline.

4) extracted_asset_type (VARCHAR): Physical hardware component (e.g., Elevator, Flooring, Door, Badge Reader, Sprinkler, HVAC Unit).

5) total_annual_unique_tickets (BIGINT): Net actionable operational tickets.

6) total_annual_duplicate_tickets (BIGINT): Redundant incident reports flagged for the same issue.

7) total_annual_critical_tickets (BIGINT): Volume of CRITICAL priority failures.

8) total_annual_critical_cost (NUMERIC): Direct expenditure dedicated strictly to critical incidents.

### The "Why Behind" This Problem

A high volume of duplicate tickets inflates workload, wastes administrative resources, and obscures true underlying issues, leading to delayed resolution of critical problems. Furthermore, without understanding annual trends in critical tickets by asset type and building, the organization cannot proactively identify deteriorating physical plant health, leading to unexpected failures, increased emergency repairs, safety risks, and unplanned CapEx surprises.

### Key Stakeholders & Pain Points

- Facilities Operations Director (Operational Continuity & Triage Workflow): Cannot accurately assess the true volume of unique maintenance demand versus "noise" from duplicate tickets, making it difficult to measure team efficiency or allocate intake staffing.

- VP of Corporate Real Estate (Portfolio Health & Life Safety): Lacks a clear annual view of escalating critical issues by building and asset type, preventing proactive CapEx planning for major renovations or equipment replacements.

- Chief Reliability Engineer (Asset Reliability & Failure Prevention): Struggles to identify specific asset types or buildings that experience a disproportionate number of critical failures annually, hindering targeted preventative programs.

### How This Model Solves It

At an annual grain, this model distinguishes net unique operational demand from duplicate ticket volume by aggregating ticket counts across building and canonical_category. It isolates priority = 'CRITICAL' failures and dimensions them by extracted_asset_type, enabling leadership to quantify redundant reporting and isolate high-risk asset classes across facilities.

### Empirical Data Grounding & Statistical Profiling

- Volume Separation: Measures 8,974 net unique tickets against 784 duplicate tickets (duplicate ticket noise: min = 0, max = 7, mean = 0.66 per cohort).

- Critical Exposure: Captures 2,077 critical priority tickets across the portfolio (min = 0, max = 16, mean = 1.76 per cohort).

- total_annual_critical_cost: Total critical emergency spend of $8,045,172.38 across 639 cohorts; Min = $0.00, Max = $71,094.93, Mean = $12,590.25.

- Asset Breakdown: Evaluates 32 distinct physical asset types, identifying major failure concentrations in structural and life-safety components (Flooring: 96 cohorts, Door: 62 cohorts, Elevator: 34 cohorts, Badge Reader: 34 cohorts, Sprinkler: 34 cohorts).

### Strategic Decisions Enabled

- Implement automated ticket submission deduplication protocols to eliminate administrative triage overhead.

- Allocate capital expenditure funds for asset replacements in facilities exhibiting escalating critical risk.

- Direct specialized maintenance resources to specific critical hardware assets (e.g., life safety and vertical transport).

- Adjust operational staffing models based on net unique workload rather than inflated raw ticket counts.

### Domain 2: Asset Reliability & Sourcing Performance (Maintenance Engineering)

### Model 3: gold.annual_asset_failure_analysis

- Table Type: Materialized View

- Dataset Shape: 2,334 rows × 10 columns

- Dimensional Grain: incident_year × building × extracted_asset_type × extracted_asset_id × canonical_category × root_cause

- Schema Definition:

1) incident_year (INT): Operational calendar year.

2) building (TEXT): Campus facility.

3) extracted_asset_type (VARCHAR): Physical asset class (Elevator, Security Camera, Flooring, Door, Plumbing Fixture).

4) extracted_asset_id (VARCHAR): Machine-level identifier (e.g., E-048, H-012).

5) canonical_category (VARCHAR): Trade discipline.

6) root_cause (VARCHAR): Diagnosed engineering failure mechanism (faulty wiring, corrosion, power surge, clogged filter, normal wear).

7) total_incidents (BIGINT): Total recorded breakdown events for this component and cause.

8) avg_resolution_time_hours (DOUBLE PRECISION): Mean turnaround duration in hours for completed repairs.

9) total_cost (NUMERIC): Direct financial expenditure incurred to remediate this specific failure.

10) total_hours_on_site (NUMERIC): Hands-on field labor hours expended.

### The "Why Behind" This Problem

Unidentified "bad actor" assets or recurring failures lead to escalating maintenance costs, unplanned downtime, reduced operational capacity, and potential safety hazards. Without understanding which assets fail most often and why, organizations cannot implement targeted preventative maintenance, optimize asset replacement cycles, or negotiate warranty terms with manufacturers.

### Key Stakeholders & Pain Points

- Chief Reliability Engineer (Asset Uptime & Lifecycle Engineering): Drowning in raw ticket logs, unable to systematically identify which specific assets fail repeatedly, what the underlying failure causes are, or how failure rates trend annually across buildings.

- Maintenance Supervisor (Technician Dispatch & First-Time Fix): Lacks a consolidated view of recurring issues for specific assets, making it difficult to prioritize proactive maintenance, identify technician training needs, or justify equipment replacements.

### How This Model Solves It

By aggregating silver.cleaned_tickets at the component level (extracted_asset_type, extracted_asset_id) combined with engineering diagnostics (root_cause), this model maps equipment breakdowns to root failure mechanisms, turnaround durations, and repair costs, converting raw maintenance logs into actionable reliability intelligence.

### Empirical Data Grounding & Statistical Profiling

- Incident Coverage: Analyzes 9,758 total equipment breakdown incidents across 2,334 cohorts (min = 1, max = 51, mean = 4.18 incidents per cohort).

- Repair Turnaround (avg_resolution_time_hours): Min = 34.32 hrs, Max = 13,209.17 hrs, Mean = 4,962.03 hrs (0 negative duration anomalies across all 1,011 verified completed repair cohorts).

- Remediation Spend (total_cost): Total tracked component repair expenditure of $8,377,457.04 across 769 cost cohorts; Min = $0.00, Max = $83,695.63, Mean = $10,893.96.

- Field Labor (total_hours_on_site): Min = 0.60 hrs, Max = 22.80 hrs, Mean = 5.15 hrs (741.50 total labor hours logged).

- Top Failure Mechanisms Diagnosed:

1) Faulty wiring: 80 cohorts

2) Corrosion: 72 cohorts

3) Power surge: 72 cohorts

4) Clogged filter: 67 cohorts

5) Normal wear: 65 cohorts

- High-Failure Asset Classes: Vertical transport (Elevator: 280 cohorts) and access systems (Security Camera: 238 cohorts).

### Strategic Decisions Enabled

- Prioritize capital expenditure for replacing chronically failing equipment identified by specific asset IDs.

- Refine preventative maintenance schedules targeting prevalent root causes (e.g., filter replacement intervals, anti-corrosion treatments).

- Hold equipment vendors accountable during warranty disputes using documented failure histories.

- Optimize spare parts inventory for components associated with frequent power surges and wiring faults.

### Model 4: gold.annual_maintenance_workforce_performance

- Table Type: Materialized View

- Dataset Shape: 373 rows × 8 columns

- Dimensional Grain: incident_year × building × canonical_category × assigned_to_group

- Schema Definition:

1) incident_year (INT): Operational calendar year (2024, 2025).

2) building (TEXT): Campus facility.

3) canonical_category (VARCHAR): Trade discipline.

4) assigned_to_group (TEXT): Workforce sourcing category (Internal in-house technician crew vs. Vendor third-party contractor).

5) total_incidents (BIGINT): Number of maintenance work orders assigned.

6) avg_resolution_time_hours (DOUBLE PRECISION): Average ticket resolution duration in hours.

7) total_labor_hours (NUMERIC): Total hands-on labor hours logged on site.

8) total_costs (NUMERIC): Direct operational costs associated with resolution.

### The "Why Behind" This Problem

Inefficient allocation of maintenance resources between internal crews and external vendors inflates operating expenses and degrades service delivery. Without objective comparative metrics on turnaround times, labor hours, and repair costs segmented by workforce tier, leadership cannot make informed insourcing vs. outsourcing decisions, evaluate contractor value, or identify internal training needs.

### Key Stakeholders & Pain Points

- Procurement & Vendor Contract Lead (Contract Negotiation & SLA Compliance): Lacks objective, data-backed metrics to compare the efficiency, cost, and responsiveness of third-party vendors against in-house teams to justify contract renewals or renegotiate rate cards.

- Maintenance Supervisor (Workforce Optimization & Labor Allocation): Cannot benchmark internal technician crews against external contractors across specific trade disciplines to identify productivity gaps or optimize team scheduling.

### How This Model Solves It

This model segments ticket assignments into Internal versus Vendor cohorts across buildings, trade categories, and operational years. It tracks workload volume, resolution turnaround duration, on-site labor hours, and total costs, providing an empirical baseline for vendor SLA compliance and sourcing trade-offs.

### Empirical Data Grounding & Statistical Profiling

- Cohort Distribution: 281 Internal technician cohorts vs. 92 Vendor contractor cohorts across 1,146 categorized incidents.

- Volume Handled:

1) Internal Teams: 1,030 incidents (Mean = 3.67 incidents/cohort, Max = 11).

2) Contracted Vendors: 116 incidents (Mean = 1.26 incidents/cohort, Max = 3).

- Turnaround Efficiency (avg_resolution_time_hours):

1) Internal Teams: Mean = 4,472.06 hrs (Min = 53.64 hrs, Max = 10,437.03 hrs).

2) Contracted Vendors: Mean = 5,188.26 hrs (Min = 82.38 hrs, Max = 12,677.26 hrs).

3) Insight: Internal teams resolve tickets an average of 716.20 hours (~30 days) faster than external contractors across comparable categories.

- Labor Intensity (total_labor_hours):

1) Internal Teams: 354.70 total on-site hours across 66 cohorts (Mean = 5.37 hrs, Max = 22.80 hrs).

2) Contracted Vendors: 43.40 total on-site hours across 10 cohorts (Mean = 4.34 hrs, Max = 7.00 hrs).

- Cost of Resolution (total_costs):

1) Internal Teams: $3,925,108.78 total spend across 246 cohorts (Mean = $15,955.73/cohort, Max = $51,433.80).

2) Contracted Vendors: $405,155.36 total spend across 60 cohorts (Mean = $6,752.59/cohort, Max = $23,585.23).

3) Combined: Min = $0.00, Max = $51,433.80, Mean = $14,151.19 (0 negative values).

### Strategic Decisions Enabled

- Optimize insourcing vs. outsourcing allocations by shifting high-delay categories to in-house teams.

- Renegotiate vendor contracts using concrete turnaround data (e.g., establishing SLA penalties when vendor resolution duration exceeds 5,000 hours).

- Identify trade disciplines where specialized contractors deliver cost efficiencies over internal crews.

- Rebalance internal technician staffing in locations where high ticket volume strains turnaround times.

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