import pandas as pd
from src.db import engine

# Query silver.cleaned_tickets directly
df = pd.read_sql("SELECT ticket_id, canonical_category, priority, status, building FROM silver.cleaned_tickets LIMIT 10;", con=engine)
print(df)