"""Conservative single-statement SQL compiler; native dry-run supplies authoritative type."""

from __future__ import annotations
import hashlib
import json
import math
import re
from validation import require, text

WRITES = {"write"}
FUNCTIONS = {
    "COUNT",
    "SUM",
    "MIN",
    "MAX",
    "AVG",
    "COALESCE",
    "IFNULL",
    "NULLIF",
    "IF",
    "CAST",
    "SAFE_CAST",
    "CONCAT",
    "LOWER",
    "UPPER",
    "TRIM",
    "LENGTH",
    "ABS",
    "ROUND",
    "FLOOR",
    "CEIL",
    "CURRENT_TIMESTAMP",
    "CURRENT_DATE",
    "DATE",
    "TIMESTAMP",
    "DATETIME",
    "EXTRACT",
    "ARRAY_LENGTH",
    "UNNEST",
    "STRUCT",
    "ARRAY",
    "IN",
    "VALUES",
    "INSERT",
    "OVER",
    "AS",
    "ROW_NUMBER",
    "RANK",
    "DENSE_RANK",
    "WITH",
    "SELECT",
}


def compile_command(name, args, config, context, connection_id):
    require(name in {"query", "write"}, "Unknown action.", code="unknown_action")
    require(set(args) <= {"connection_ref", "statement", "parameters"}, "Unknown action input.")
    statement = text(args.get("statement"), "statement", 65536, nonempty=True)
    require(
        not any(s in statement for s in (";", "--", "/*", "*/", "{{", "}}", "\\")),
        "Scripts, comments and template SQL are unsupported.",
    )
    # Ignore single/double-quoted literal contents before scanning identifiers.
    scrub = re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"", "''", statement)
    kind = re.match(r"\s*([A-Za-z]+)", scrub)
    require(kind is not None, "One SQL statement is required.")
    allowed = {"SELECT"} if name == "query" else {"INSERT", "UPDATE", "DELETE", "MERGE"}
    require(
        (kind.group(1).upper() if kind else "") in allowed,
        "Use the action matching the SQL statement type.",
    )
    require(
        not re.search(
            r"\b(EXTERNAL_QUERY|EXPORT|LOAD|CREATE|ALTER|DROP|CALL|EXECUTE|GRANT|REVOKE|BEGIN|COMMIT|ROLLBACK|MODEL|CONNECTION)\b",
            scrub,
            re.I,
        ),
        "Unsupported SQL capability.",
    )
    # Physical table targets are always explicit backticked identifiers. This
    # also avoids relying on potentially incomplete provider reference metadata.
    references = re.findall(r"`([^`]+)`", scrub)
    require(len(references) < 50, "At most 49 explicit table references are supported.")
    for target in re.finditer(
        r"\b(FROM|JOIN|USING|UPDATE|INSERT|DELETE|INTO|MERGE)\s+(`[^`]+`|[A-Za-z_][A-Za-z0-9_]*|\()",
        scrub,
        re.I,
    ):
        keyword, identifier = target.group(1).upper(), target.group(2)
        if (keyword, identifier.upper()) in {("INSERT", "INTO"), ("DELETE", "FROM")}:
            continue  # Optional INTO/FROM introducers are checked separately.
        if kind is not None and kind.group(1).upper() == "MERGE":
            if (keyword, identifier.upper()) in {("INSERT", "ROW"), ("DELETE", "WHEN")}:
                continue  # MERGE action clauses introduce no additional table.
        if keyword == "UPDATE" and identifier.upper() == "SET":
            continue  # MERGE WHEN MATCHED THEN UPDATE SET ...
        if keyword == "MERGE" and identifier.upper() == "INTO":
            continue  # The INTO token is checked separately.
        if identifier.upper() == "UNNEST" and scrub[target.end() :].lstrip().startswith("("):
            continue
        require(
            identifier.startswith("`") or identifier == "(",
            "Physical tables must use fully qualified backticked identifiers.",
        )
    # Comma joins would introduce additional physical targets without a JOIN
    # keyword. Require explicit JOIN syntax; commas inside functions stay valid.
    depth = 0
    from_levels: set[int] = set()
    for lexeme in re.findall(r"`[^`]*`|[A-Za-z_][A-Za-z0-9_]*|[^\s]", scrub):
        keyword = lexeme.upper()
        if lexeme == "(":
            depth += 1
        elif lexeme == ")":
            from_levels.discard(depth)
            depth = max(0, depth - 1)
        elif keyword == "FROM":
            from_levels.add(depth)
        elif keyword in {
            "WHERE",
            "GROUP",
            "ORDER",
            "LIMIT",
            "HAVING",
            "QUALIFY",
            "WINDOW",
            "UNION",
            "EXCEPT",
            "INTERSECT",
        }:
            from_levels.discard(depth)
        elif lexeme == ",":
            require(depth not in from_levels, "Use explicit JOIN syntax with backticked tables.")
    for identifier in references:
        require(
            re.fullmatch(
                r"[a-z][a-z0-9-]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", identifier
            ),
            "Use fully qualified ordinary table identifiers.",
        )
        project, dataset, _ = identifier.split(".")
        require(
            project == config["project"] and dataset in config["allowed_datasets"],
            "Table is outside the connection allowlist.",
        )
    for function in re.findall(r"([\w.]+)\s*\(", scrub):
        require(function.upper() in FUNCTIONS, "Only supported built-in functions are allowed.")
    params = args.get("parameters", [])
    require(
        isinstance(params, list) and len(params) <= 32, "At most 32 typed parameters are supported."
    )
    metadata, values = [], {}
    for param in params:
        require(
            isinstance(param, dict) and set(param) == {"name", "type", "value"},
            "Each parameter needs name, type and value.",
        )
        key = param["name"]
        require(
            isinstance(key, str)
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key)
            and key not in values,
            "Invalid or duplicate parameter name.",
        )
        typ, value = param["type"], param["value"]
        require(
            typ in {"STRING", "INT64", "FLOAT64", "BOOL"},
            "Supported types: STRING, INT64, FLOAT64, BOOL.",
        )
        if typ == "STRING":
            text(value, "parameter", 65536)
        if typ == "INT64":
            require(
                type(value) is int and abs(value) <= 9007199254740991,
                "INT64 parameters must be safe JSON integers.",
            )
        if typ == "FLOAT64":
            require(
                type(value) in {int, float}
                and math.isfinite(value)
                and (type(value) is not int or abs(value) <= 9007199254740991),
                "Finite precision-safe float required.",
            )
        if typ == "BOOL":
            require(type(value) is bool, "Boolean parameter required.")
        metadata.append(
            {
                "name": key,
                "type": {
                    "STRING": "string",
                    "INT64": "integer",
                    "FLOAT64": "float",
                    "BOOL": "boolean",
                }[typ],
                "description": "Typed workflow parameter.",
            }
        )
        values[key] = value
    require(
        set(re.findall(r"@([A-Za-z_][A-Za-z0-9_]*)", scrub)) == set(values),
        "Named parameters must match SQL exactly.",
    )
    require("?" not in scrub, "Only named parameters are supported.")
    if name == "write":
        claim = context.get("external_effect_id")
        if isinstance(claim, str) and claim.strip():
            identity = [
                context.get("account_id"),
                context.get("project_id"),
                connection_id,
                claim,
                statement,
                params,
            ]
            config["job_id"] = (
                "fs_"
                + hashlib.sha256(
                    json.dumps(
                        identity, sort_keys=True, separators=(",", ":"), allow_nan=False
                    ).encode()
                ).hexdigest()
            )
    config["tables"] = list(dict.fromkeys(references))
    config["write_mode"] = "allowed" if name == "write" else "blocked"
    return {"type": "bigquery-sql", "statement": statement, "parameters": metadata}, values


def normalize_result(name, value, args):
    require(
        isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict),
        "Invalid BigQuery job response.",
        code="invalid_provider_response",
    )
    result = value[0]
    require(
        isinstance(result.get("rows"), list)
        and len(result["rows"]) <= 100
        and isinstance(result.get("job_id"), str),
        "Invalid bounded job response.",
        code="invalid_provider_response",
    )
    return result
