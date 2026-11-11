import json
from src.agents.catalog import TICKETS_CATALOG
from src.agents.generic_profiler import profile_catalog_dataset
from src.agents.data_quality import run_generalized_dq_agent

if __name__ == "__main__":
    # 1. Profile using the comprehensive catalog
    profile_data = profile_catalog_dataset(
        catalog=TICKETS_CATALOG,
        table_schema="bronze",
        table_name="raw_tickets"
    )

    # 2. Print Cross-Column Violations
    print("=" * 80)
    print("CROSS-COLUMN & TABLE CONSTRAINT VIOLATIONS")
    print("=" * 80)
    for name, details in profile_data["cross_column_violations"].items():
        print(f"\nConstraint: {name}")
        print(f"  Description: {details.get('description')}")
        print(f"  Violations Found: {details.get('violation_count')} ({details.get('violation_rate')})")
        if details.get("sample_violations"):
            print(f"  Sample Records: {details.get('sample_violations')}")

    # 3. Print Specific Column Checks
    print("\n" + "=" * 80)
    print("KEY COLUMN ANOMALIES IDENTIFIED BY CATALOG SPEC")
    print("=" * 80)
    for col, data in profile_data["column_profiles"].items():
        violations = data.get("catalog_rule_violations", {})
        has_violations = any(v.get("violation_count", 0) > 0 for v in violations.values() if isinstance(v, dict))
        if has_violations:
            print(f"\nColumn: [{col}] -> Target: {data['target_type']}")
            for chk_name, chk_res in violations.items():
                if isinstance(chk_res, dict) and chk_res.get("violation_count", 0) > 0:
                    print(f"  * {chk_name}: {chk_res['violation_count']} rows | Samples: {chk_res.get('sample_values')}")

    # 4. Run Generalized Agent
    print("\n" + "=" * 80)
    print("RUNNING LLM REASONING ON COMPLETE CATALOG & EMPIRICAL PROFILE...")
    print("=" * 80)
    report = run_generalized_dq_agent(TICKETS_CATALOG, profile_data)

    print(f"\n[Executive Summary]:\n{report.executive_summary}\n")
    print(f"[Column Transformations Generated ({len(report.column_rules)})]:")
    for r in report.column_rules:
        print(f"\n{r.column_name} ({r.target_type}) [{r.severity}]:")
        print(f"  Anomalies: {r.anomalies_detected}")
        print(f"  SQL: {r.sql_transformation}")