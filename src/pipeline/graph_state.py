from typing import Dict, Any, List, Optional, TypedDict
from pydantic import BaseModel
from src.agents.data_quality import ComprehensiveAuditReport


class SilverPipelineMetrics(BaseModel):
    bronze_total_rows: int = 0
    silver_total_rows: int = 0
    swapped_repaired_count: int = 0
    taxonomy_classes_mapped: int = 0
    description_entities_cached: int = 0
    resolution_entities_cached: int = 0
    temporal_anomalies_flagged: int = 0
    duplicates_flagged: int = 0


class PipelineState(TypedDict):
    # Execution inputs
    bronze_schema: str
    bronze_table: str
    silver_schema: str
    silver_table: str
    llm_batch_limit: int

    # Agent 1 (Data Quality) Artifacts
    profile_data: Optional[Dict[str, Any]]
    dq_audit_report: Optional[ComprehensiveAuditReport]

    # Agent 2 (Semantic) Artifacts
    swapped_candidates_count: int
    distinct_categories_count: int
    extracted_descriptions_stats: Dict[str, int]
    extracted_resolutions_count: int

    # Final Execution Metrics & Status
    metrics: SilverPipelineMetrics
    pipeline_status: str
    execution_logs: List[str]