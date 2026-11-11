# Gold Analytical Models: Full Detail

> Full per-model detail (schemas, stakeholders, empirical profiling, decisions enabled).
> The README holds the summary; nothing was removed, only moved here.

## Gold Analytical Models & Business Value

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
