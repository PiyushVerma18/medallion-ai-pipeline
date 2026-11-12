# Gold Analytical Models and Business Value

Agent 3 examines the Silver schema and sample values, then proposes two annual-grain models for each configured business domain. It generates a PostgreSQL query for each model and runs the result in a staging table. The staging row count and numeric summaries are passed to the statistical audit. A model is promoted only when the audit passes.

The descriptions below follow the business dossiers generated in the supplied run. The dimensions, metrics, SQL, and names are agent proposals and can change with the Silver input, prompt, or LLM cache. The profiling numbers are observations from this run, not permanent guarantees about future data.

## Domain 1: Corporate Real Estate and Facilities Operations

### Model 1: `gold.annual_facilities_opex_summary`

**Dimensional grain:** `incident_year` × `building` × `canonical_category` × `assigned_to`

The model aggregates annual facilities tickets by year, facility, normalized maintenance category, and assigned team or vendor. The proposed measures are total cost, on-site hours, and unique ticket count.

### The "Why Behind" This Problem

Uncontrolled facilities OPEX can lead to budget overruns and leave less funding for planned maintenance or strategic improvements. Raw ticket logs do not make it easy to see which buildings, maintenance disciplines, or assigned teams account for the most cost and effort. Without that view, leaders may allocate budgets reactively, miss opportunities for preventative maintenance, or renegotiate vendor contracts without evidence about where spending is concentrated.

### Key Stakeholders & Pain Points

- **VP of Corporate Real Estate:** Needs to manage total cost of ownership, facility uptime, and alignment with portfolio strategy. The raw ticket data does not make disproportionate building or category costs easy to identify, which makes it harder to justify capital investment or shift budgets.
- **Facilities Operations Director:** Needs to run daily maintenance, manage teams and vendors, and control operating budgets. The volume of tickets makes it difficult to find high-spend areas and demonstrate the return on preventative maintenance.
- **Corporate Financial Controller:** Needs reliable cost reporting and forecasting. Ticket-level records lack the categorized, consolidated view needed to challenge budget requests or identify savings opportunities.

### How This Model Solves It

The proposed annual grouping brings costs, on-site effort, and ticket counts together at building, category, and assignee level. This lets facilities and finance teams compare where money and work are concentrated, identify unusually expensive cohorts, and investigate whether resources or preventative maintenance should be shifted. It also creates a basis for vendor discussions using recorded costs and activity.

### Empirical Data Grounding & Statistical Profiling

The staged query returned **2,517 rows**, spanning incident years **2023–2025** (average year 2024.47). Its audited measures were non-negative:

- `total_annual_cost`: minimum **$0.00**, maximum **$101,541.86**, average **$16,241.44**.
- `total_annual_hours_on_site`: minimum **0.60**, maximum **22.80**, average **5.05 hours**.
- `total_unique_tickets`: minimum **0**, maximum **24**, average **3.57**.

The staging statistical audit passed, and Agent 3 promoted the model. The audit checks basic statistical plausibility; it does not independently verify that the cost field represents all OPEX or that the ticket-count definition matches a finance reporting policy.

### Strategic Decisions Enabled

- Reallocate maintenance budgets toward buildings and categories with persistent demand or high cost.
- Investigate facilities that may need modernization or capital investment.
- Review vendor contracts against observed cost and workload patterns.
- Target preventative maintenance where the annual ticket and cost profile suggests recurring demand.
- Compare staffing and service effort across buildings and maintenance categories.

### Model 2: `gold.annual_critical_risk_summary`

**Dimensional grain:** `incident_year` × `building` × `canonical_category` × `extracted_asset_type`

This model focuses on tickets with `priority = 'CRITICAL'`. The dossier proposes annual critical-ticket counts, associated costs, and average resolution hours by facility, category, and asset type.

### The "Why Behind" This Problem

Escalating critical maintenance issues can signal equipment failure, safety exposure, regulatory risk, or growing downtime. If critical tickets are viewed only as individual records, it is difficult to see whether the same facilities or asset classes repeatedly generate high-risk incidents. That can leave maintenance reactive and delay capital planning or focused preventative work.

### Key Stakeholders & Pain Points

- **VP of Corporate Real Estate:** Needs to understand portfolio health, safety, and asset value. Without an annual view of critical issues across properties, it is difficult to prioritize facility upgrades or demonstrate that risks are being managed.
- **Facilities Operations Director:** Needs to keep facilities safe and available. Ticket volume can obscure recurring critical patterns, leading to crisis response instead of planned mitigation.
- **Chief Reliability Engineer:** Needs to extend asset life and reduce operational risk. Raw records make it difficult to isolate which buildings or asset types repeatedly produce critical failures.

### How This Model Solves It

By restricting the proposed model to critical-priority tickets and grouping by year, building, category, and extracted asset type, the model highlights where critical issues recur. Ticket counts show frequency; cost and resolution-hour measures add impact context. Leaders can use these cohorts to prioritize risk reviews, maintenance plans, specialist resources, or capital upgrades.

### Empirical Data Grounding & Statistical Profiling

The staged query returned **816 rows**, spanning incident years **2024–2025** (average year 2024.43). The audited measures were non-negative:

- `total_critical_tickets`: minimum **1**, maximum **16**, average **2.55**.
- `total_critical_cost`: minimum **$0.00**, maximum **$71,094.93**, average **$12,492.50**.
- `avg_critical_resolution_hours`: minimum **61.7**, maximum **13,412.61**, average **5,078.35 hours**.

The statistical audit passed, and the model was promoted. The very high maximum resolution duration is a signal for human review of timestamp quality, duration semantics, and the suitability of an average; non-negativity alone does not mean the value is operationally reasonable.

### Strategic Decisions Enabled

- Prioritize capital work for facilities and asset classes with recurring critical incidents.
- Establish focused preventative maintenance for high-risk buildings or equipment.
- Conduct safety and compliance reviews where critical tickets cluster.
- Assign specialist teams to recurring critical problems.
- Track whether mitigation reduces critical-ticket frequency, cost, or resolution time over time.

## Domain 2: Asset Reliability and Sourcing Performance

### Model 3: `gold.annual_asset_failure_summary`

**Dimensional grain:** `incident_year` × `building` × `canonical_category` × `extracted_asset_type` × `extracted_asset_id` × `root_cause`

This model analyzes recurring equipment failures at asset and cause level. The proposal reports incident counts, cost of failure, average resolution hours, and on-site hours.

### The "Why Behind" This Problem

Assets that fail repeatedly can drive repair costs, downtime, shorter useful lives, and safety concerns. If recurring failures and their suspected causes are buried in free-text ticket notes, reliability teams may not identify bad-actor equipment or recurring failure modes soon enough to plan repairs, replacement, or warranty action.

### Key Stakeholders & Pain Points

- **Chief Reliability Engineer:** Needs to improve uptime and asset lifecycle performance. Raw ticket logs make it difficult to identify repeatedly failing asset IDs, compare failure causes, or quantify their cost.
- **Maintenance Supervisors:** Need to prioritize technicians, work orders, and spare parts. Without an aggregated view of failure patterns and repair effort, work is more likely to remain reactive.

### How This Model Solves It

The proposed grain combines year, facility, asset identity, maintenance category, and extracted root cause. Aggregated incident count, repair cost, resolution time, and field hours help distinguish frequent failures from costly or slow-to-resolve ones. Reliability teams can use those patterns to target root-cause investigation and maintenance planning.

### Empirical Data Grounding & Statistical Profiling

The staged query returned **682 rows**, spanning incident years **2024–2025** (average year 2024.27). Audited numeric measures were non-negative:

- `total_incidents`: minimum **1**, maximum **11**, average **1.68**.
- `total_cost_of_failure`: minimum **$0.00**, maximum **$50,219.88**, average **$8,855.35**.
- `average_resolution_time_hours`: minimum **53.64**, maximum **13,412.61**, average **5,141.56 hours**.
- `total_hours_on_site`: minimum **0.60**, maximum **22.80**, average **4.91 hours**.

The audit passed and the model was promoted. The maximum resolution duration is unusually large relative to on-site hours; validate the business meaning and timestamp inputs before interpreting it as repair turnaround.

### Strategic Decisions Enabled

- Prioritize investigation or replacement of assets with repeated failures and high costs.
- Direct preventative maintenance toward frequent root causes.
- Support warranty or supplier discussions with asset-level failure history.
- Plan spare-parts inventory around recurring equipment problems.
- Compare failure patterns by facility, category, and asset type to identify systemic issues.

### Model 4: `gold.annual_workforce_performance_summary`

**Dimensional grain:** `incident_year` × `assigned_to` workforce group × `building` × `canonical_category`

The proposed model categorizes assignees as Internal or External Vendor and compares annual resolved-ticket counts, labor cost, resolution time, on-site hours, and first-time fixes.

### The "Why Behind" This Problem

Maintenance work may be split between internal teams and external vendors, but without comparable measures it is difficult to judge sourcing, staffing, or contract performance. Missing visibility into workload, resolution time, labor cost, and repeat fixes can contribute to overspending or missed service expectations. Comparisons also need to account for the fact that teams may handle different types and levels of ticket complexity.

### Key Stakeholders & Pain Points

- **Procurement and Vendor Contract Lead:** Needs evidence to evaluate vendor service and negotiate contracts. Ticket-level data does not provide an easy comparison of vendor and internal cost, speed, or outcome patterns.
- **VP of Corporate Real Estate / Facilities Director:** Needs to balance cost and service levels across the workforce. Without aggregated performance information, it is difficult to assess the internal/external mix or find staffing gaps.

### How This Model Solves It

The proposed annual view groups activity by workforce type, facility, and maintenance category. It combines ticket volume with cost, resolution time, on-site hours, and first-time-fix count. This supports structured comparisons and helps leaders identify where to investigate workload allocation, training, staffing, or vendor terms. It is descriptive evidence, not a causal or automatically fair ranking of workers or suppliers.

### Empirical Data Grounding & Statistical Profiling

The staged query returned **385 rows**, spanning incident years **2024–2025** (average year 2024.31). All audited numeric measures were non-negative:

- `total_tickets_resolved`: minimum **1**, maximum **8**, average **2.07**.
- `total_cost_of_labor`: minimum **$0.00**, maximum **$52,673.61**, average **$10,215.60**.
- `average_resolution_time_hours`: minimum **74.41**, maximum **13,045.84**, average **5,006.93 hours**.
- `total_hours_on_site`: minimum **0.60**, maximum **22.80**, average **5.03 hours**.
- `first_time_fixes_count`: minimum **0**, maximum **4**, average **0.49**.

The statistical audit passed and the model was promoted. The large resolution-hour maximum warrants review. Compare workforce groups only after checking ticket mix, assignment classification, missing values, and whether these metrics use consistent denominators.

### Strategic Decisions Enabled

- Review the balance of internal and external maintenance capacity by building and trade.
- Investigate vendor cohorts with high cost, long resolution times, or low first-time-fix counts.
- Identify internal training or staffing needs by maintenance category.
- Inform contract discussions with workload and cost evidence.
- Benchmark similar facilities and work categories while accounting for differences in ticket mix.

## Agent audit and human review

In the supplied run, each staging result had rows and passed the statistical audit; all four models were promoted. The audit examined row counts and numeric summaries, including negative values. It did not verify the business correctness of every metric, detect every plausible outlier, or establish causality. Before using a model for financial, safety, asset, or workforce decisions, review its generated SQL, filters, grouping grain, treatment of duplicates and anomalies, null handling, and metric denominators.
