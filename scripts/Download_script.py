import pandas as pd
from src.db import engine

# Query the materialized silver table
query = "SELECT * FROM silver.cleaned_tickets;"

#print("Exporting golden dataset...")
df = pd.read_sql(query, con=engine)

import os
os.makedirs("exports", exist_ok=True)
output_path = "exports/silver_cleaned_tickets_new.csv"
df.to_csv(output_path, index=False)

#print(f"Dataset successfully exported to {output_path} ({len(df)} rows).")

