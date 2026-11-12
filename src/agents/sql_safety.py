"""Conservative guards for SQL text returned by agents before execution."""

import re


_COMMENTS_OR_DOLLAR_QUOTES = re.compile(r"--|/\*|\*/|\$[A-Za-z_0-9]*\$")
_SINGLE_QUOTED = re.compile(r"'(?:''|[^'])*'")
_WRITE_OR_CONTROL_KEYWORDS = re.compile(
    r"\b(?:insert|update|delete|merge|copy|call|do|execute|alter|create|drop|truncate|"
    r"grant|revoke|comment|vacuum|analyze|refresh|set|reset|begin|commit|rollback|"
    r"savepoint|prepare|deallocate|into)\b",
    re.IGNORECASE,
)
_SIDE_EFFECT_FUNCTIONS = re.compile(
    r"\b(?:nextval|setval|dblink(?:_[a-z_]+)?|lo_import|lo_unlink|pg_read_file|"
    r"pg_read_binary_file|pg_ls_dir|pg_sleep)\s*\(",
    re.IGNORECASE,
)
_RELATION_REFERENCE = re.compile(
    r"\b(?:from|join)\s+(?:only\s+)?(?:([a-z_][a-z0-9_]*)\s*\.\s*)?([a-z_][a-z0-9_]*)\b",
    re.IGNORECASE,
)
_QUALIFIED_REFERENCE = re.compile(r"\b([a-z_][a-z0-9_]*)\s*\.\s*[a-z_][a-z0-9_]*\b", re.IGNORECASE)
_CTE_NAME = re.compile(r"(?:\bwith\b|,)\s*([a-z_][a-z0-9_]*)\s+as\s*\(", re.IGNORECASE)
_EXTRACT_EXPRESSION = re.compile(r"\bextract\s*\([^)]*\)", re.IGNORECASE)
_GOLD_TARGET = re.compile(r"^gold\.[a-z][a-z0-9_]*$", re.IGNORECASE)


def _code_only(sql: str) -> str:
    if _COMMENTS_OR_DOLLAR_QUOTES.search(sql):
        raise ValueError("Agent SQL cannot contain SQL comments or dollar-quoted strings")
    return _SINGLE_QUOTED.sub(" ", sql)


def validate_agent_expression(expression: str) -> str:
    """Allow a scalar SQL expression for Silver; reject statement-capable text."""
    candidate = expression.strip()
    code = _code_only(candidate)
    if ";" in code:
        raise ValueError("Agent-generated Silver expressions must be a single expression")
    if _WRITE_OR_CONTROL_KEYWORDS.search(code) or re.search(r"\b(?:select|with|from|join)\b", code, re.I):
        raise ValueError("Agent-generated Silver SQL must be a scalar expression, not a statement or subquery")
    if _SIDE_EFFECT_FUNCTIONS.search(code):
        raise ValueError("Agent-generated Silver SQL contains a disallowed side-effect function")
    for schema in _QUALIFIED_REFERENCE.findall(code):
        if schema.lower() != "raw":
            raise ValueError(f"Agent-generated Silver expression may only qualify source fields with raw., found {schema}.")
    return candidate


def validate_gold_target(target_name: str) -> str:
    target = target_name.strip()
    if not _GOLD_TARGET.fullmatch(target) or target.lower().split(".", 1)[1].startswith("_staging_"):
        raise ValueError(f"Agent-generated Gold target must be a safe gold schema name, got {target_name!r}")
    return target


def validate_gold_select(select_sql: str) -> str:
    """Require a single read-only SELECT rooted only in the Silver ticket table."""
    candidate = select_sql.strip()
    code = _code_only(candidate)
    code = code.rstrip()
    if code.endswith(";"):
        code = code[:-1].rstrip()
        candidate = candidate.rstrip()[:-1].rstrip()
    if ";" in code:
        raise ValueError("Agent-generated Gold SQL must contain exactly one statement")
    if not re.match(r"^(?:select|with)\b", code, re.IGNORECASE):
        raise ValueError("Agent-generated Gold SQL must be a SELECT query")
    if _WRITE_OR_CONTROL_KEYWORDS.search(code):
        raise ValueError("Agent-generated Gold SQL contains a write or control statement")
    if _SIDE_EFFECT_FUNCTIONS.search(code):
        raise ValueError("Agent-generated Gold SQL contains a disallowed side-effect function")
    cte_names = {name.lower() for name in _CTE_NAME.findall(code)}
    # EXTRACT uses the SQL keyword FROM as part of its expression syntax, not
    # as a table clause; mask it before checking source relations.
    relation_scan = _EXTRACT_EXPRESSION.sub(" ", code)
    for match in _RELATION_REFERENCE.finditer(relation_scan):
        schema, relation = match.groups()
        relation = relation.lower()
        if schema:
            schema = schema.lower()
            if schema in {"bronze", "gold"}:
                raise ValueError(f"Gold agent SQL cannot access the {schema} schema")
            if schema != "silver" or relation != "cleaned_tickets":
                raise ValueError(f"Gold agent SQL may only read silver.cleaned_tickets, found {schema}.{relation}")
        elif relation not in cte_names:
            raise ValueError(f"Gold agent SQL may only read silver.cleaned_tickets or its own CTEs, found {relation}")
    return candidate
