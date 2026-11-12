-- Run as a database administrator. Safe to re-run.
-- Creates stage roles and grants each stage only its required layer access.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'medallion_silver') THEN
        CREATE ROLE medallion_silver NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'medallion_gold') THEN
        CREATE ROLE medallion_gold NOLOGIN;
    END IF;
END
$$;

GRANT medallion_silver, medallion_gold TO pipeline_user;

GRANT USAGE ON SCHEMA bronze TO medallion_silver, medallion_gold;
GRANT SELECT ON ALL TABLES IN SCHEMA bronze TO medallion_silver, medallion_gold;
ALTER DEFAULT PRIVILEGES FOR ROLE pipeline_user IN SCHEMA bronze
    GRANT SELECT ON TABLES TO medallion_silver, medallion_gold;

GRANT USAGE, CREATE ON SCHEMA silver TO medallion_silver;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA silver TO medallion_silver;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA silver TO medallion_silver;
ALTER DEFAULT PRIVILEGES FOR ROLE medallion_silver IN SCHEMA silver
    GRANT ALL PRIVILEGES ON TABLES TO medallion_silver;

GRANT USAGE ON SCHEMA silver TO medallion_gold;
GRANT SELECT ON ALL TABLES IN SCHEMA silver TO medallion_gold;
ALTER DEFAULT PRIVILEGES FOR ROLE medallion_silver IN SCHEMA silver
    GRANT SELECT ON TABLES TO medallion_gold;

GRANT USAGE, CREATE ON SCHEMA gold TO medallion_gold;

-- Existing Silver objects must be owned by the Silver role so its normal
-- CREATE/ALTER/UPSERT work remains possible after switching away from admin.
DO $$
DECLARE object_row RECORD;
BEGIN
    FOR object_row IN
        SELECT tablename FROM pg_tables WHERE schemaname = 'silver'
    LOOP
        EXECUTE format('ALTER TABLE silver.%I OWNER TO medallion_silver', object_row.tablename);
    END LOOP;
    FOR object_row IN
        SELECT sequencename FROM pg_sequences WHERE schemaname = 'silver'
    LOOP
        EXECUTE format('ALTER SEQUENCE silver.%I OWNER TO medallion_silver', object_row.sequencename);
    END LOOP;
    FOR object_row IN
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'gold' AND left(tablename, 9) = '_staging_'
    LOOP
        EXECUTE format('ALTER TABLE gold.%I OWNER TO medallion_gold', object_row.tablename);
    END LOOP;
    FOR object_row IN
        SELECT matviewname FROM pg_matviews WHERE schemaname = 'gold'
    LOOP
        EXECUTE format('ALTER MATERIALIZED VIEW gold.%I OWNER TO medallion_gold', object_row.matviewname);
    END LOOP;
END
$$;
