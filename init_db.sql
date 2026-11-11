-- Create distinct Medallion schemas
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

-- Grant usage and permissions to pipeline user
GRANT ALL ON SCHEMA bronze TO pipeline_user;
GRANT ALL ON SCHEMA silver TO pipeline_user;
GRANT ALL ON SCHEMA gold TO pipeline_user;

ALTER DEFAULT PRIVILEGES IN SCHEMA bronze GRANT ALL ON TABLES TO pipeline_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA silver GRANT ALL ON TABLES TO pipeline_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA gold GRANT ALL ON TABLES TO pipeline_user;