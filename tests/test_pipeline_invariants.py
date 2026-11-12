import pytest
from sqlalchemy import text
from src.db import engine

def test_bronze_lineage():
    """Verify Bronze table preserves raw data and full lineage metadata."""
    with engine.connect() as conn:
        res = conn.execute(text("""
            SELECT 
                COUNT(*) AS total,
                COUNT(_row_hash) AS hashes,
                COUNT(_source_file) AS files,
                COUNT(_ingested_at) AS timestamps
            FROM bronze.raw_tickets;
        """)).mappings().one()
        assert res["total"] > 0
        assert res["total"] == res["hashes"]
        assert res["total"] == res["files"]
        assert res["total"] == res["timestamps"]

def test_silver_deduplication_and_swaps():
    """Verify Silver layer deduplication and field-swap alignment."""
    with engine.connect() as conn:
        res = conn.execute(text("""
            SELECT 
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE is_field_swapped = TRUE) AS swapped,
                COUNT(*) FILTER (WHERE is_duplicate = TRUE) AS duplicates,
                COUNT(*) FILTER (WHERE is_temporal_anomaly = TRUE) AS anomalies
            FROM silver.cleaned_tickets;
        """)).mappings().one()
        assert res["total"] == 10280
        assert res["swapped"] == 46
        assert res["duplicates"] == 823
        assert res["anomalies"] == 1866

def test_gold_metrics_non_negative():
    """Verify numeric columns in every currently promoted Gold materialized view."""
    with engine.connect() as conn:
        view_names = conn.execute(text("""
            SELECT matviewname
            FROM pg_matviews
            WHERE schemaname = 'gold'
            ORDER BY matviewname;
        """)).scalars().all()
        assert view_names, "No Gold materialized views have been promoted."

        preparer = conn.dialect.identifier_preparer
        gold_schema = preparer.quote("gold")
        for view_name in view_names:
            cols = conn.execute(text(f"""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_schema = 'gold' AND table_name = :view_name
                  AND data_type IN ('numeric', 'double precision', 'real', 'integer', 'bigint');
            """), {"view_name": view_name}).scalars().all()
            
            quoted_view = preparer.quote(view_name)
            for col in cols:
                quoted_column = preparer.quote(col)
                neg_count = conn.execute(text(f"""
                    SELECT COUNT(*) FROM {gold_schema}.{quoted_view}
                    WHERE {quoted_column} < 0;
                """)).scalar()
                assert neg_count == 0, f"Negative value detected in gold.{view_name}.{col}!"
