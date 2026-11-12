"""Export promoted Gold models to CSV files for review and sharing."""

from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
from sqlalchemy import text

from src.db import gold_engine
from src.pipeline.stage_logging import log_stage
from src.agents.sql_safety import validate_gold_target


@log_stage("gold.export_csv")
def export_promoted_gold_models(models: List[Any], output_dir: str = "exports") -> Dict[str, Any]:
    """Write each promoted Gold materialized view to its own CSV file."""
    export_path = Path(output_dir)
    export_path.mkdir(parents=True, exist_ok=True)
    exported = []

    with gold_engine.connect() as conn:
        for model in models:
            if not model.is_promoted:
                continue

            target = validate_gold_target(model.problem.target_table_name)
            model_name = target.split(".", 1)[1]
            destination = export_path / f"gold_{model_name}.csv"
            frame = pd.read_sql_query(text(f"SELECT * FROM {target}"), con=conn)
            frame.to_csv(destination, index=False)
            exported.append({"file": str(destination), "rows": len(frame)})
            print(f"[Gold Export] Wrote {len(frame):,} rows to {destination}")

    return {"exported_models": len(exported), "exports": exported}
