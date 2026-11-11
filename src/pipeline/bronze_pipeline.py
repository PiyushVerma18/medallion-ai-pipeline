import hashlib
import os
import pandas as pd
from sqlalchemy import text
from src.db import engine

BRONZE_DDL = """
CREATE SCHEMA IF NOT EXISTS bronze;

CREATE TABLE IF NOT EXISTS bronze.raw_tickets (
    _row_hash VARCHAR(64) PRIMARY KEY,
    _source_file VARCHAR(255) NOT NULL,
    _ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ticket_id TEXT,
    created_at TEXT,
    resolved_at TEXT,
    category TEXT,
    priority TEXT,
    status TEXT,
    building TEXT,
    description TEXT,
    submitted_by TEXT,
    assigned_to TEXT,
    resolution_notes TEXT,
    cost TEXT,
    sla_hours TEXT
);
"""

def compute_row_hash(row: pd.Series) -> str:
    """Computes a deterministic MD5 hash across all raw columns."""
    row_str = "|".join(str(val) if pd.notna(val) else "" for val in row)
    return hashlib.md5(row_str.encode("utf-8")).hexdigest()

def ingest_bronze(file_path: str = "data/raw_tickets.csv") -> dict:
    """Ingests raw CSV data into bronze.raw_tickets with full lineage metadata."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Source file not found at: {file_path}")

    print(f"[Bronze] Reading raw file from: {file_path}")
    df = pd.read_csv(file_path, dtype=str)
    raw_row_count = len(df)

    # Compute row hashes and lineage columns
    df["_row_hash"] = df.apply(compute_row_hash, axis=1)
    df["_source_file"] = os.path.basename(file_path)

    # Ensure schema and table exist
    with engine.begin() as conn:
        conn.execute(text(BRONZE_DDL))

    print("[Bronze] Writing records to bronze staging...")
    # Keep the same connection for temp table creation, bulk copy, and upsert
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("CREATE TEMP TABLE temp_stage_bronze (LIKE bronze.raw_tickets INCLUDING DEFAULTS) ON COMMIT DROP;"))
            
            # Write directly on this active transaction connection
            df.to_sql("temp_stage_bronze", con=conn, if_exists="append", index=False)

            upsert_sql = """
            INSERT INTO bronze.raw_tickets (
                _row_hash, _source_file, _ingested_at,
                ticket_id, created_at, resolved_at, category, priority,
                status, building, description, submitted_by, assigned_to,
                resolution_notes, cost, sla_hours
            )
            SELECT 
                _row_hash, _source_file, CURRENT_TIMESTAMP,
                ticket_id, created_at, resolved_at, category, priority,
                status, building, description, submitted_by, assigned_to,
                resolution_notes, cost, sla_hours
            FROM temp_stage_bronze
            ON CONFLICT (_row_hash) DO NOTHING;
            """
            result = conn.execute(text(upsert_sql))
            inserted_rows = result.rowcount

            total_bronze = conn.execute(text("SELECT COUNT(*) FROM bronze.raw_tickets;")).scalar()

    print(f"[Bronze] Ingestion complete. Raw rows: {raw_row_count} | New rows inserted: {inserted_rows} | Total rows in bronze: {total_bronze}")

    return {
        "source_rows": raw_row_count,
        "new_inserted": inserted_rows,
        "total_bronze_records": total_bronze,
    }

if __name__ == "__main__":
    ingest_bronze()