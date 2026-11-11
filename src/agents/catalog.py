from typing import List, Optional
from pydantic import BaseModel, Field


class CheckCondition(BaseModel):
    name: str
    description: str
    sql_violation_condition: str = Field(
        description="SQL boolean expression that evaluates to TRUE when a record VIOLATES the rule."
    )


class ColumnSpec(BaseModel):
    name: str
    description: str
    target_type: str
    is_nullable: bool = True
    format_regex: Optional[str] = None
    allowed_values: Optional[List[str]] = None
    known_sentinels: Optional[List[str]] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    custom_checks: List[CheckCondition] = []


class TableConstraint(BaseModel):
    name: str
    description: str
    sql_violation_condition: str


class DatasetCatalog(BaseModel):
    dataset_name: str
    business_domain: str
    table_constraints: List[TableConstraint] = []
    columns: List[ColumnSpec]


TICKETS_CATALOG = DatasetCatalog(
    dataset_name="facility_support_tickets",
    business_domain="Enterprise facility operations and maintenance ticketing",
    table_constraints=[
        TableConstraint(
            name="temporal_integrity_resolved_after_created",
            description=(
                "Tickets cannot be resolved before creation. "
                "Flag violations in Silver as is_temporal_anomaly = TRUE; do NOT delete raw records."
            ),
            # Safe diagnostic condition checking ISO timestamp inversions
            sql_violation_condition="""
                created_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                AND resolved_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                AND (resolved_at::timestamptz < created_at::timestamptz)
            """
        ),
        TableConstraint(
            name="closed_ticket_requires_resolved_timestamp",
            description="Tickets marked 'Resolved' or 'Closed' should possess a valid resolution timestamp.",
            sql_violation_condition="""
                status IN ('Resolved', 'Closed') 
                AND (resolved_at IS NULL OR resolved_at IN ('never', 'pending', '???', 'TBD', '00/00/0000'))
            """
        )
    ],
    columns=[
        ColumnSpec(
            name="ticket_id",
            description="Unique ticket primary key. Must match 'TKT-<integer>'.",
            target_type="VARCHAR(50)",
            is_nullable=False,
            format_regex=r"^TKT-\d+$",
            custom_checks=[
                CheckCondition(
                    name="invalid_id_format",
                    description="Ticket IDs not matching 'TKT-<integer>'.",
                    sql_violation_condition="ticket_id IS NOT NULL AND ticket_id !~ '^TKT-[0-9]+$'"
                )
            ]
        ),
        ColumnSpec(
            name="created_at",
            description="Timestamp of ticket creation. Must capture all date formats with zero data loss.",
            target_type="TIMESTAMP",
            is_nullable=False,
            known_sentinels=["00/00/0000", "asap", "not a date", "TBD"],
            custom_checks=[
                CheckCondition(
                    name="epoch_timestamps",
                    description="10-digit Unix epoch timestamps.",
                    sql_violation_condition="created_at ~ '^[0-9]{10}$'"
                ),
                CheckCondition(
                    name="alpha_month_dates",
                    description="Dates with alpha months (e.g. 28-Feb-2024 17:03).",
                    sql_violation_condition="created_at ~ '^[0-9]{2}-[A-Za-z]{3}-[0-9]{4}'"
                ),
                CheckCondition(
                    name="slash_dates",
                    description="US slash dates with or without time (e.g. 02/03/2025).",
                    sql_violation_condition="created_at ~ '^[0-9]{2}/[0-9]{2}/[0-9]{4}'"
                ),
                CheckCondition(
                    name="hyphen_mdy_dates",
                    description="Hyphenated dates like MM-DD-YYYY HH24:MI:SS.",
                    sql_violation_condition="created_at ~ '^[0-9]{2}-[0-9]{2}-[0-9]{4}'"
                ),
                CheckCondition(
                    name="iso_date_only",
                    description="ISO dates lacking time (e.g. 2025-04-03).",
                    sql_violation_condition="created_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'"
                ),
                CheckCondition(
                    name="unparseable_sentinels",
                    description="Known textual sentinels that should become NULL.",
                    sql_violation_condition="created_at IN ('00/00/0000', 'asap', 'not a date', 'TBD')"
                )
            ]
        ),
        ColumnSpec(
            name="resolved_at",
            description="Timestamp of ticket resolution. Can be NULL for open tickets.",
            target_type="TIMESTAMP",
            is_nullable=True,
            known_sentinels=["???", "never", "pending", "TBD", "00/00/0000"],
            custom_checks=[
                CheckCondition(
                    name="epoch_timestamps",
                    description="10-digit Unix epoch timestamps.",
                    sql_violation_condition="resolved_at ~ '^[0-9]{10}$'"
                ),
                CheckCondition(
                    name="alpha_month_dates",
                    description="Dates with alpha months (e.g. 12-Feb-2025 03:21).",
                    sql_violation_condition="resolved_at ~ '^[0-9]{2}-[A-Za-z]{3}-[0-9]{4}'"
                ),
                CheckCondition(
                    name="slash_dates",
                    description="US slash dates with or without time.",
                    sql_violation_condition="resolved_at ~ '^[0-9]{2}/[0-9]{2}/[0-9]{4}'"
                ),
                CheckCondition(
                    name="hyphen_mdy_dates",
                    description="Hyphenated dates like MM-DD-YYYY HH24:MI:SS.",
                    sql_violation_condition="resolved_at ~ '^[0-9]{2}-[0-9]{2}-[0-9]{4}'"
                ),
                CheckCondition(
                    name="iso_date_only",
                    description="ISO dates lacking time.",
                    sql_violation_condition="resolved_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'"
                ),
                CheckCondition(
                    name="unparseable_sentinels",
                    description="Known placeholders that should become NULL.",
                    sql_violation_condition="resolved_at IN ('???', 'never', 'pending', 'TBD', '00/00/0000')"
                )
            ]
        ),
        ColumnSpec(
            name="category",
            description="Raw category text. DO NOT trim, truncate, swap, or nullify here. Agent 2 handles semantic classification and swap repair.",
            target_type="TEXT",
            is_nullable=False
        ),
        ColumnSpec(
            name="description",
            description="Raw description text. DO NOT truncate or nullify here. Agent 2 handles semantic parsing and swap repair.",
            target_type="TEXT",
            is_nullable=False
        ),
        ColumnSpec(
            name="priority",
            description="Severity tier. Standardize to 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW'. Map 'normal' to 'MEDIUM'. Default NULL/'???' to 'MEDIUM'.",
            target_type="VARCHAR(20)",
            is_nullable=False,
            allowed_values=["CRITICAL", "HIGH", "MEDIUM", "LOW"],
            known_sentinels=["urgent!!!", "ASAP", "hi", "crit", "med", "lo", "normal", "???"]
        ),
        ColumnSpec(
            name="status",
            description="Ticket lifecycle status. Normalize to Title Case ('Open', 'In Progress', 'Pending Vendor', 'Escalated', 'Resolved', 'Closed'). Sentinels ('???', 'unknown') or NULL become 'Unassigned'.",
            target_type="VARCHAR(30)",
            is_nullable=False,
            allowed_values=["Open", "In Progress", "Pending Vendor", "Escalated", "Resolved", "Closed"]
        ),
        ColumnSpec(
            name="building",
            description="Facility name. Test artifacts ('???', 'unknown', 'test building') become NULL.",
            target_type="VARCHAR(100)",
            is_nullable=True,
            known_sentinels=["???", "unknown", "test building"]
        ),
        ColumnSpec(
            name="submitted_by",
            description="Submitter name. Clean whitespace.",
            target_type="VARCHAR(100)",
            is_nullable=True
        ),
        ColumnSpec(
            name="assigned_to",
            description="Assigned technician/vendor. Clean whitespace.",
            target_type="VARCHAR(100)",
            is_nullable=True
        ),
        ColumnSpec(
            name="resolution_notes",
            description="Technician notes. Clean whitespace.",
            target_type="TEXT",
            is_nullable=True
        ),
        ColumnSpec(
            name="cost",
            description="Repair expense in USD. Strip '$' and commas. Nullify negative values (< 0, -1, -999) and errors ('error', 'TBD').",
            target_type="NUMERIC(10,2)",
            is_nullable=True,
            min_value=0.0,
            known_sentinels=["-1", "-999", "error", "TBD"]
        ),
        ColumnSpec(
            name="sla_hours",
            description="SLA limit in hours. Nullify sentinel codes (999, 999.0, -1, -1.0, 0, 0.0) and values <= 0.",
            target_type="NUMERIC(6,1)",
            is_nullable=True,
            min_value=1.0,
            known_sentinels=["999", "999.0", "-1", "-1.0", "0", "0.0"]
        )
    ]
)