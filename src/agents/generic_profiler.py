from typing import Dict, Any
from sqlalchemy import text
from src.db import engine
from src.agents.catalog import DatasetCatalog


def profile_catalog_dataset(
    catalog: DatasetCatalog, 
    table_schema: str = "bronze", 
    table_name: str = "raw_tickets"
) -> Dict[str, Any]:
    """
    Catalog-Driven Profiler:
    Uses the DatasetCatalog specifications, regexes, and table constraints to run
    targeted validation queries on PostgreSQL safely without aborting transactions.
    """
    full_table = f"{table_schema}.{table_name}"
    print(f"\n[Catalog Profiler] Starting inspection of {full_table} based on '{catalog.dataset_name}' catalog...")

    # Using autocommit isolation mode ensures each query is an independent transaction
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        total_rows = conn.execute(text(f"SELECT COUNT(*) FROM {full_table};")).scalar() or 0

        profile_report = {
            "dataset_name": catalog.dataset_name,
            "business_domain": catalog.business_domain,
            "table": full_table,
            "total_rows": total_rows,
            "cross_column_violations": {},
            "column_profiles": {}
        }

        # 1. Profile Table Constraints (Cross-Column Rules)
        print("[Catalog Profiler] Checking cross-column table constraints...")
        for constraint in catalog.table_constraints:
            check_sql = f"""
                SELECT COUNT(*) AS violation_count
                FROM {full_table}
                WHERE {constraint.sql_violation_condition};
            """
            try:
                viol_count = conn.execute(text(check_sql)).scalar() or 0
                sample_viol_sql = f"""
                    SELECT ticket_id, created_at, resolved_at, status 
                    FROM {full_table} 
                    WHERE {constraint.sql_violation_condition}
                    LIMIT 3;
                """
                samples = [dict(r) for r in conn.execute(text(sample_viol_sql)).mappings().all()]
                profile_report["cross_column_violations"][constraint.name] = {
                    "description": constraint.description,
                    "violation_count": viol_count,
                    "violation_rate": f"{round((viol_count / total_rows * 100), 2)}%" if total_rows > 0 else "0%",
                    "sample_violations": samples
                }
            except Exception as e:
                profile_report["cross_column_violations"][constraint.name] = {
                    "description": constraint.description,
                    "error": f"Failed check query: {str(e).splitlines()[0]}"
                }

        # 2. Profile Each Column based on ColumnSpec
        print(f"[Catalog Profiler] Profiling {len(catalog.columns)} columns against expectations...")
        for col in catalog.columns:
            # Baseline Stats
            base_sql = f"""
                SELECT
                    COUNT(*) FILTER (WHERE "{col.name}" IS NULL) AS null_count,
                    COUNT(DISTINCT "{col.name}") AS distinct_count,
                    MIN(LENGTH("{col.name}")) AS min_len,
                    MAX(LENGTH("{col.name}")) AS max_len
                FROM {full_table};
            """
            try:
                base_stats = dict(conn.execute(text(base_sql)).mappings().first())
            except Exception as e:
                base_stats = {"null_count": 0, "distinct_count": 0, "min_len": 0, "max_len": 0}

            null_count = base_stats["null_count"]
            null_pct = round((null_count / total_rows * 100), 2) if total_rows > 0 else 0

            # Sample Top Values
            top_sql = f"""
                SELECT "{col.name}" AS val, COUNT(*) AS cnt
                FROM {full_table}
                WHERE "{col.name}" IS NOT NULL
                GROUP BY "{col.name}"
                ORDER BY cnt DESC
                LIMIT 5;
            """
            try:
                top_samples = {str(r["val"]): r["cnt"] for r in conn.execute(text(top_sql)).mappings().all()}
            except Exception:
                top_samples = {}

            # Run Custom Catalog Checks
            custom_violations = {}
            for chk in col.custom_checks:
                chk_sql = f"""
                    SELECT COUNT(*) AS v_count
                    FROM {full_table}
                    WHERE {chk.sql_violation_condition};
                """
                try:
                    v_count = conn.execute(text(chk_sql)).scalar() or 0
                    
                    sample_vals_sql = f"""
                        SELECT DISTINCT "{col.name}" AS sample_val
                        FROM {full_table}
                        WHERE {chk.sql_violation_condition}
                        LIMIT 5;
                    """
                    sample_vals = conn.execute(text(sample_vals_sql)).scalars().all()
                    
                    custom_violations[chk.name] = {
                        "description": chk.description,
                        "violation_count": v_count,
                        "sample_values": sample_vals
                    }
                except Exception as e:
                    custom_violations[chk.name] = {"error": str(e).splitlines()[0]}

            profile_report["column_profiles"][col.name] = {
                "target_type": col.target_type,
                "null_count": null_count,
                "null_pct": f"{null_pct}%",
                "distinct_count": base_stats["distinct_count"],
                "length_range": [base_stats["min_len"], base_stats["max_len"]],
                "top_frequent_samples": top_samples,
                "catalog_rule_violations": custom_violations
            }

    print(f"[Catalog Profiler] Profiling complete across all constraints and columns.\n")
    return profile_report