from typing import Dict, Any, List, TypedDict, Optional
from pydantic import BaseModel
from langgraph.graph import StateGraph, START, END
from src.agents.gold_closed_loop_agent import (
    agent_understand_schema,
    introspect_silver_column_context,
    agent_formulate_domain_problems,
    agent_generate_and_self_verify_sql,
    execute_dry_run_and_extract_stats,
    agent_audit_gold_stats,
    promote_staging_to_production_view,
    SchemaSemanticUnderstanding,
    GoldProblemStatement,
    VerifiedSqlSolution,
    StatAuditResult
)

# =========================================================================
# State Definitions
# =========================================================================

class DomainConfig(BaseModel):
    domain_name: str
    narrative: str

class ModelTask(BaseModel):
    domain_name: str
    problem: GoldProblemStatement
    current_retry: int = 0
    sql_solution: Optional[VerifiedSqlSolution] = None
    dry_run_stats: Optional[Dict[str, Any]] = None
    audit_result: Optional[StatAuditResult] = None
    is_promoted: bool = False
    row_count: int = 0
    final_row_count: int = 0  # <--- Add this field here
    error: Optional[str] = None

class MultiDomainClosedLoopState(TypedDict):
    columns_list: List[str]
    domains: List[DomainConfig]
    max_healing_retries: int
    schema_understanding: Optional[SchemaSemanticUnderstanding]
    models: List[ModelTask]
    current_model_idx: int
    execution_history: List[str]


# =========================================================================
# Graph Nodes
# =========================================================================

def node_understand_schema(state: MultiDomainClosedLoopState) -> Dict[str, Any]:
    print("\n" + "=" * 80)
    print("▶ [Step 1/4: Schema Understanding] Introspecting Column Profiles & Top Samples...")
    print("=" * 80)
    
    # 1. Dynamically fetch types and top distinct values directly from database
    column_context = introspect_silver_column_context("silver.cleaned_tickets", sample_limit=5)
    print(f"✔ Extracted distinct samples across {len(column_context)} columns from database.")
    
    # 2. Feed empirical context to LLM
    understanding = agent_understand_schema(column_context)
    print(f"✔ Grounded LLM understanding across all {len(understanding.column_mappings)} columns.")
    
    return {
        "schema_understanding": understanding,
        "execution_history": state.get("execution_history", []) + ["Schema semantics decoded with real data samples."]
    }


def node_formulate_problems(state: MultiDomainClosedLoopState) -> Dict[str, Any]:
    print("\n" + "=" * 80)
    print(f"▶ [Step 2/4: Problem Formulation] Generating Deep Business Problem Dossiers...")
    print("=" * 80)
    
    models: List[ModelTask] = []
    for d in state["domains"]:
        print(f"\n" + "#" * 80)
        print(f"🏢 DOMAIN: {d.domain_name}")
        print("#" * 80)
        
        problems = agent_formulate_domain_problems(
            schema_understanding=state["schema_understanding"],
            domain_name=d.domain_name,
            business_domain_narrative=d.narrative
        )
        
        for idx, p in enumerate(problems, start=1):
            print(f"\n[{idx}] PROBLEM: {p.problem_title}")
            print(f"    Target Model    : {p.target_table_name}")
            print(f"\n    📌 THE 'WHY BEHIND' THIS PROBLEM:")
            print(f"       {p.why_this_problem_matters}")
            
            print(f"\n    👥 KEY STAKEHOLDERS:")
            for s in p.key_stakeholders:
                print(f"       • {s.title} ({s.primary_mandate})")
                print(f"         Pain Point: {s.current_pain_point}")
                
            print(f"\n    💡 HOW THIS MODEL SOLVES IT:")
            print(f"       {p.how_model_solves_problem}")
            
            print(f"\n    🎯 DECISIONS ENABLED:")
            for dec in p.decisions_enabled:
                print(f"       ✔ {dec}")
            print("-" * 80)
            
            models.append(ModelTask(domain_name=d.domain_name, problem=p))

    print(f"\n✔ Total {len(models)} models queued with complete operational dossiers.")
    return {
        "models": models,
        "current_model_idx": 0,
        "execution_history": state.get("execution_history", []) + [f"Queued {len(models)} models across domains."]
    }


def node_generate_sql(state: MultiDomainClosedLoopState) -> Dict[str, Any]:
    idx = state["current_model_idx"]
    m = state["models"][idx]
    
    print("\n" + "-" * 80)
    print(f"▶ Generating SQL for Model [{idx + 1}/{len(state['models'])}]: {m.problem.target_table_name} (Attempt {m.current_retry + 1})...")
    print("-" * 80)

    feedback = None
    if m.audit_result and not m.audit_result.is_statistically_sound:
        feedback = "; ".join(m.audit_result.detected_violations)
        if m.audit_result.suggested_sql_patch:
            feedback += f" Suggested fix: {m.audit_result.suggested_sql_patch}"
        print(f"⚠️ Passing Error Feedback to Agent: {feedback}")

    sql_sol = agent_generate_and_self_verify_sql(
        problem_stmt=m.problem,
        schema_understanding=state["schema_understanding"],
        error_feedback=feedback
    )
    m.sql_solution = sql_sol
    print(f"✔ Agent generated SQL. Verification Rationale: {sql_sol.self_verification_rationale[:100]}...")
    
    return {"models": state["models"]}


def node_dry_run_and_audit(state: MultiDomainClosedLoopState) -> Dict[str, Any]:
    idx = state["current_model_idx"]
    m = state["models"][idx]
    
    print(f"\n▶ Dry-running staging view for {m.problem.target_table_name}...")
    stats_data = execute_dry_run_and_extract_stats(m.problem.target_table_name, m.sql_solution.raw_query_sql)
    m.dry_run_stats = stats_data
    m.current_retry += 1
    
    print(f"✔ Staging row count: {stats_data['row_count']}")

    # =========================================================================
    # CASE 1: ZERO ROWS RETURNED (Direct Deterministic Feedback)
    # =========================================================================
    if stats_data["row_count"] == 0:
        print("✖ STATISTICAL VIOLATIONS DETECTED: row_count is 0")
        m.audit_result = StatAuditResult(
            is_statistically_sound=False,
            detected_violations=["Staging table has 0 rows (empty dataset returned)"],
            suggested_sql_patch=(
                "Your WHERE clause filtered out all records! "
                "Check every filter in your WHERE clause against the `sample_values` provided in the schema context. "
                "Verify string casing and formatting (e.g. check whether column values are ALL-CAPS, lowercase, or Title Case)."
            )
        )
        return {"models": state["models"]}

    # =========================================================================
    # CASE 2: ROWS EXIST -> PROCEED WITH METRIC AUDITING
    # =========================================================================
    for col, s in stats_data.get("numeric_stats", {}).items():
        avg_str = f"{s['avg']:.2f}" if s.get("avg") is not None else "None"
        print(f"   • {col:<32}: min={s['min']}, max={s['max']}, avg={avg_str}, negatives={s['negative_count']}")
    
    

    print("\n[Agent Auditor] Inspecting statistical sanity...")
    audit = agent_audit_gold_stats(stats_data)
    m.audit_result = audit

    if audit.is_statistically_sound:
        print("✔ ALL METRIC AUDITS PASSED! Values are statistically sound.")
    else:
        print(f"✖ STATISTICAL VIOLATIONS DETECTED:")
        for v in audit.detected_violations:
            print(f"   - {v}")

    return {"models": state["models"]}


def route_audit_decision(state: MultiDomainClosedLoopState) -> str:
    """Conditional Edge: Self-heal if violations exist, else promote and advance."""
    idx = state["current_model_idx"]
    m = state["models"][idx]
    max_retries = state.get("max_healing_retries", 3)

    if m.audit_result and m.audit_result.is_statistically_sound:
        return "promote_and_advance"
    
    if m.current_retry < max_retries:
        print(f"\n🔄 Routing to Self-Healing Loop for {m.problem.target_table_name} (Retry {m.current_retry}/{max_retries})...")
        return "self_heal"
    
    print(f"\n⚠️ Max retries reached for {m.problem.target_table_name}. Moving forward.")
    return "promote_and_advance"


def node_promote_and_advance(state: MultiDomainClosedLoopState) -> Dict[str, Any]:
    idx = state["current_model_idx"]
    m = state["models"][idx]

    if m.audit_result and m.audit_result.is_statistically_sound:
        final_count = promote_staging_to_production_view(m.problem.target_table_name, m.sql_solution.raw_query_sql)
        m.is_promoted = True
        m.final_row_count = final_count
        print(f"🚀 SUCCESS: Promoted '{m.problem.target_table_name}' ({final_count:,} rows) to Gold!")
    else:
        m.is_promoted = False
        m.error = "; ".join(m.audit_result.detected_violations if m.audit_result else ["Unknown error"])
        print(f"✖ FAILED: '{m.problem.target_table_name}' had unresolved violations.")

    next_idx = idx + 1
    return {
        "models": state["models"],
        "current_model_idx": next_idx,
        "execution_history": state.get("execution_history", []) + [
            f"Model {m.problem.target_table_name}: {'PROMOTED' if m.is_promoted else 'FAILED'}"
        ]
    }


def route_next_model_or_finish(state: MultiDomainClosedLoopState) -> str:
    """Checks if more models remain in the queue."""
    if state["current_model_idx"] < len(state["models"]):
        return "next_model"
    return "finish"


# =========================================================================
# Graph Construction
# =========================================================================

def build_multi_domain_closed_loop_pipeline() -> StateGraph:
    graph = StateGraph(MultiDomainClosedLoopState)

    graph.add_node("understand_schema", node_understand_schema)
    graph.add_node("formulate_problems", node_formulate_problems)
    graph.add_node("generate_sql", node_generate_sql)
    graph.add_node("dry_run_and_audit", node_dry_run_and_audit)
    graph.add_node("promote_and_advance", node_promote_and_advance)

    graph.add_edge(START, "understand_schema")
    graph.add_edge("understand_schema", "formulate_problems")
    graph.add_edge("formulate_problems", "generate_sql")
    graph.add_edge("generate_sql", "dry_run_and_audit")

    # Healing edge
    graph.add_conditional_edges(
        "dry_run_and_audit",
        route_audit_decision,
        {
            "self_heal": "generate_sql",
            "promote_and_advance": "promote_and_advance"
        }
    )

    # Next model edge
    graph.add_conditional_edges(
        "promote_and_advance",
        route_next_model_or_finish,
        {
            "next_model": "generate_sql",
            "finish": END
        }
    )

    return graph.compile()