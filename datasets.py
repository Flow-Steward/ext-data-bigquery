"""Read-only saved SQL adapter over the packaged BigQuery transport.

The dataset dialect deliberately supports one physical table, explicit column
projections, simple predicates, ORDER BY and LIMIT. Unsupported SQL fails closed;
workflow query/write actions retain their existing independent grammar.
"""

from __future__ import annotations

import json
import re
import time
from typing import cast

import toolbox
from commands import compile_command, normalize_result
from validation import Failure, connection_values, integer, require, text

ACTIONS = frozenset(
    {
        "test_connection",
        "introspect_schema",
        "validate_query",
        "preview_query",
        "execute_template",
        "infer_output_schema",
    }
)
IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]{0,63}"
RESERVED = frozenset(
    {
        "SELECT",
        "FROM",
        "WHERE",
        "AS",
        "ORDER",
        "BY",
        "LIMIT",
        "ASC",
        "DESC",
        "AND",
        "OR",
        "NOT",
        "IS",
        "NULL",
        "IN",
        "TRUE",
        "FALSE",
        "UNION",
    }
)
TABLE = r"[a-z][a-z0-9-]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*"
TOKEN = re.compile(
    r"\s+|'(?:[^'\\]|'')*'|`[^`\\]*`|:[A-Za-z_][A-Za-z0-9_]*|[A-Za-z_][A-Za-z0-9_]*|-?[0-9]+(?:\.[0-9]+)?|<=|>=|!=|<>|[(),=<>]"
)


def catalog() -> list[dict]:
    return [
        {
            "connector_id": "bigquery",
            "connector_type_id": "bigquery",
            "database_kind": "bigquery",
            "display_name": "BigQuery",
            "source_kind": "bigquery",
            "source_extension_id": "flowsteward.bigquery",
            "source_extension_name": "BigQuery",
            "source_provider": "BigQuery",
            "query_language": "sql",
            "access_mode": "read_only",
        }
    ]


def query(name: object) -> dict:
    if name == "supported_connectors":
        return {"connectors": catalog()}
    if name == "runtime_health":
        toolbox._binary()
        return {"status": "healthy", "extension_id": "flowsteward.bigquery"}
    raise Failure("unknown_query", "Unknown query.")


def _tokens(sql: str) -> list[str]:
    tokens, offset = [], 0
    for match in TOKEN.finditer(sql):
        require(match.start() == offset, "Unsupported saved SQL syntax.")
        token = match.group()
        if not token.isspace():
            tokens.append(token)
        offset = match.end()
    require(offset == len(sql) and 1 <= len(tokens) <= 2048, "Unsupported or oversized saved SQL.")
    return tokens


class _Select:
    """Small closed grammar: every column token is checked at its use site."""

    def __init__(self, sql: str, resources: dict):
        self.tokens = _tokens(sql)
        self.index = 0
        self.parameters: set[str] = set()
        self.columns: list[str] = []
        self.lineage: dict[str, str] = {}
        # The FROM introducer cannot occur inside a string or quoted identifier.
        positions = [i for i, token in enumerate(self.tokens) if token.upper() == "FROM"]
        require(len(positions) == 1, "One physical table is required.")
        position = positions[0]
        require(position + 1 < len(self.tokens), "Physical table required.")
        physical = self.tokens[position + 1]
        require(
            physical.startswith("`") and re.fullmatch(TABLE, physical[1:-1]),
            "Use a fully qualified backticked table.",
        )
        self.table = physical[1:-1]
        require(self.table in resources, "Table is outside dataset resource scope.")
        self.allowed = resources[self.table]

    def peek(self) -> str:
        return self.tokens[self.index].upper() if self.index < len(self.tokens) else ""

    def take(self, token: str) -> bool:
        if self.peek() == token:
            self.index += 1
            return True
        return False

    def expect(self, token: str) -> None:
        require(self.take(token), "Unsupported saved SELECT grammar.")

    def column(self) -> str:
        require(self.index < len(self.tokens), "Expected scoped column.")
        token = self.tokens[self.index]
        name = token[1:-1] if token.startswith("`") else token
        require(
            re.fullmatch(IDENTIFIER, name) and name.upper() not in RESERVED,
            "Only ordinary non-keyword scoped columns are supported.",
        )
        require(self.allowed is None or name in self.allowed, "Column is outside dataset scope.")
        # Backticks here are column identifiers, not physical table references.
        self.tokens[self.index] = name
        self.index += 1
        return name

    def value(self) -> None:
        require(self.index < len(self.tokens), "Expected predicate operand.")
        token = self.tokens[self.index]
        if token.startswith(":"):
            self.parameters.add(token[1:])
            self.tokens[self.index] = "@" + token[1:]
            self.index += 1
        elif (
            token.startswith("'")
            or re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", token)
            or token.upper() in {"TRUE", "FALSE", "NULL"}
        ):
            self.index += 1
        else:
            self.column()

    def predicate(self, depth: int = 0) -> None:
        require(depth <= 16, "Predicate nesting exceeds limit.")
        self.take("NOT")
        if self.take("("):
            self.expression(depth + 1)
            self.expect(")")
            return
        self.value()
        if self.take("IS"):
            self.take("NOT")
            self.expect("NULL")
        elif self.take("IN"):
            self.expect("(")
            self.value()
            count = 1
            while self.take(","):
                count += 1
                require(count <= 32, "IN operands exceed limit.")
                self.value()
            self.expect(")")
        else:
            require(
                self.peek() in {"=", "!=", "<>", "<", ">", "<=", ">="},
                "Only comparison predicates are supported.",
            )
            self.index += 1
            self.value()

    def expression(self, depth: int = 0) -> None:
        self.predicate(depth)
        while self.peek() in {"AND", "OR"}:
            self.index += 1
            self.predicate(depth)

    def parse(self, row_limit: int) -> tuple[str, set[str]]:
        self.expect("SELECT")
        names = set()
        while True:
            source_name = self.column()
            name = source_name
            if self.take("AS"):
                require(
                    self.index < len(self.tokens)
                    and re.fullmatch(IDENTIFIER, self.tokens[self.index])
                    and self.tokens[self.index].upper() not in RESERVED,
                    "Ordinary projection alias required.",
                )
                name = self.tokens[self.index]
                self.index += 1
            require(name not in names, "Duplicate output column.")
            names.add(name)
            self.columns.append(name)
            self.lineage[name] = source_name
            require(len(names) <= 100, "At most 100 projected columns supported.")
            if not self.take(","):
                break
        self.expect("FROM")
        self.index += 1  # Physical resource was verified before parsing.
        if self.take("WHERE"):
            self.expression()
        if self.take("ORDER"):
            self.expect("BY")
            while True:
                self.column()
                if self.peek() in {"ASC", "DESC"}:
                    self.index += 1
                if not self.take(","):
                    break
        sql_limit = row_limit + 1
        limit_start = self.index
        if self.take("LIMIT"):
            require(
                self.index < len(self.tokens) and re.fullmatch(r"[0-9]+", self.tokens[self.index]),
                "LIMIT must be a bounded positive integer.",
            )
            sql_limit = min(sql_limit, integer(int(self.tokens[self.index]), "LIMIT", 1, 1000000))
            self.index += 1
        require(self.index == len(self.tokens), "Unsupported saved SELECT grammar.")
        # Rebuild from lexical tokens, never interpolate parameter values.
        tokens = self.tokens[:]
        if limit_start < len(tokens):
            tokens = tokens[:limit_start]
        return " ".join(tokens) + " LIMIT " + str(sql_limit), self.parameters


def _resources(dataset: dict) -> dict:
    scope = dataset.get("resource_scope_jsonb") or dataset.get("resource_scope") or {}
    require(isinstance(scope, dict), "Dataset resource scope is required.")
    resources = scope.get("resources")
    require(
        isinstance(resources, list) and 1 <= len(resources) <= 100,
        "Explicit dataset resources are required.",
    )
    resources = cast(list, resources)
    out = {}
    for resource in resources:
        require(isinstance(resource, dict), "Invalid dataset resource.")
        name = resource.get("resource_id")
        require(
            isinstance(name, str) and re.fullmatch(TABLE, name) and name not in out,
            "Fully qualified unique dataset resources required.",
        )
        columns = resource.get("columns")
        require(columns is None or isinstance(columns, list), "Invalid allowed columns.")
        if columns is not None:
            require(
                1 <= len(columns) <= 1000
                and all(isinstance(c, str) and re.fullmatch(IDENTIFIER, c) for c in columns),
                "Explicit ordinary columns required.",
            )
        out[name] = None if columns is None else set(columns)
    return out


def _parameters(template: dict, names: set[str], values: dict) -> list[dict]:
    schema = template.get("param_schema_jsonb") or template.get("param_schema") or {}
    require(
        isinstance(schema, dict) and isinstance(values, dict),
        "Parameter schema and values must be objects.",
    )
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    require(
        isinstance(properties, dict) and isinstance(required, list), "Invalid parameter schema."
    )
    require(
        names <= set(properties) and set(values) == names and set(required) <= set(values),
        "Parameters must match declared SQL placeholders.",
    )
    types = {"string": "STRING", "integer": "INT64", "number": "FLOAT64", "boolean": "BOOL"}
    out = []
    for name in sorted(names):
        require(isinstance(properties[name], dict), "Invalid parameter type schema.")
        typ = properties[name].get("type")
        require(
            isinstance(typ, str) and typ in types,
            "Only scalar non-null JSON parameter types supported.",
        )
        out.append({"name": name, "type": types[typ], "value": values[name]})
    return out


def _schema(
    rows: list[dict],
    lineage: dict[str, str],
    template: dict,
    dataset: dict,
    table: str,
    *,
    infer: bool,
) -> dict:
    saved = template.get("output_schema_jsonb")
    if not infer and isinstance(saved, dict) and saved.get("type") == "object":
        properties = saved.get("properties")
        if isinstance(properties, dict) and set(properties) == set(lineage):
            return saved
    types = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        dict: "object",
        list: "array",
    }
    snapshot = dataset.get("schema_snapshot_jsonb") or {}
    column_types = {}
    if isinstance(snapshot, dict) and isinstance(snapshot.get("resources"), list):
        for resource in snapshot["resources"]:
            if not isinstance(resource, dict) or resource.get("resource_id") != table:
                continue
            for column in resource.get("columns") or []:
                if isinstance(column, dict):
                    typ = column.get("value_type")
                    if typ in {"string", "integer", "number", "boolean", "object", "array"}:
                        column_types[column.get("name")] = typ
    properties = {}
    for name, source in lineage.items():
        value = next((row[name] for row in rows if row.get(name) is not None), None)
        typ = types.get(type(value), column_types.get(source))
        properties[name] = {"type": typ} if typ else {}
    return {"type": "object", "properties": properties, "additionalProperties": True}


def _budget(args: dict, name: str, default: int, maximum: int) -> int:
    limits = args.get("limits") or {}
    require(isinstance(limits, dict), "Limits must be an object.")
    return integer(limits.get(name, default), name, 1, maximum)


def handle(name: str, args: dict, context: dict) -> dict:
    config = args.get("connection_config")
    refs = args.get("secret_refs") or {}
    require(isinstance(config, dict) and isinstance(refs, dict), "Connection objects required.")
    secrets = refs.get("values") or refs.get("refs") or refs
    config, secrets = connection_values({"config": config, "secrets": secrets})
    timeout = min(
        config["request_timeout_seconds"],
        _budget(args, "timeout_seconds", config["request_timeout_seconds"], 120),
    )
    max_bytes = _budget(args, "max_result_bytes", 460 * 1024, 460 * 1024)
    if name == "introspect_schema":
        return _introspect(
            config, secrets, timeout, max_bytes, args.get("dataset") if "dataset" in args else None
        )
    if name == "test_connection":
        command, values = compile_command(
            "query", {"statement": "SELECT 1", "parameters": []}, config, context, "dataset"
        )
        normalize_result(
            "query", toolbox.invoke(config, secrets, command, values, timeout_seconds=timeout), {}
        )
        return {"status": "healthy", "connector_id": "bigquery"}
    template, dataset = args.get("query_template"), args.get("dataset")
    require(
        isinstance(template, dict) and isinstance(dataset, dict),
        "Saved template and dataset required.",
    )
    template, dataset = cast(dict, template), cast(dict, dataset)
    require(template.get("query_language", "sql") == "sql", "Only saved SQL is supported.")
    if template.get("dataset_id") is not None:
        require(
            template["dataset_id"] == dataset.get("dataset_id"),
            "Saved template dataset does not match.",
        )
    definition = template.get("query_definition_jsonb") or template.get("query_definition") or {}
    require(isinstance(definition, dict), "SQL definition must be an object.")
    sql = text(definition.get("sql"), "sql", 65536, nonempty=True)
    key = "max_runtime_rows" if name == "execute_template" else "max_preview_rows"
    limit = _budget(args, key, 100, 100)
    parser = _Select(sql, _resources(dataset))
    compiled, placeholders = parser.parse(limit)
    params = _parameters(template, placeholders, args.get("params") or {})
    command, values = compile_command(
        "query", {"statement": compiled, "parameters": params}, config, context, "dataset"
    )
    config["max_result_rows"] = limit
    result = normalize_result(
        "query", toolbox.invoke(config, secrets, command, values, timeout_seconds=timeout), {}
    )
    rows = result["rows"]
    require(
        all(isinstance(row, dict) and set(row) <= set(parser.columns) for row in rows),
        "Provider columns do not match projections.",
        code="invalid_provider_response",
    )
    require(
        len(json.dumps(rows, allow_nan=False).encode()) <= max_bytes,
        "Dataset result exceeds byte budget.",
        code="response_too_large",
    )
    truncated = (
        len(rows) > limit
        or result.get("truncated", False)
        or result.get("total_rows", len(rows)) > limit
    )
    rows = rows[:limit]
    schema = _schema(
        rows, parser.lineage, template, dataset, parser.table, infer=name == "infer_output_schema"
    )
    result = {
        "rows": rows,
        "row_count": len(rows),
        "output_schema": schema,
        "truncated": bool(truncated),
    }
    if name == "validate_query":
        result.update({"valid": True, "read_only": True, "referenced_resources": [parser.table]})
    if name == "infer_output_schema":
        return {"output_schema": schema}
    return result


def _introspect(
    config: dict, secrets: dict, timeout: int, max_bytes: int, selected: object = None
) -> dict:
    deadline = time.monotonic() + timeout
    resources: list[dict] = []
    columns_seen = 0
    tables_seen = 0
    scoped = {**config, "write_mode": "blocked", "tables": []}
    selection = None
    if selected is not None:
        require(isinstance(selected, dict), "Selected Dataset must be an object.")
        selection = _resources(cast(dict, selected))
        for resource_id in selection:
            project, dataset_name, _ = resource_id.split(".")
            require(
                project == config["project"] and dataset_name in config["allowed_datasets"],
                "Selected Dataset resource is outside the connection allowlist.",
            )

    def invoke(tool: str, values: dict) -> list:
        remaining = deadline - time.monotonic()
        require(remaining > 0, "Schema introspection deadline exceeded.", code="timeout")
        return toolbox.invoke(scoped, secrets, {"type": tool}, values, timeout_seconds=remaining)

    for dataset in config["allowed_datasets"]:
        if selection is None:
            ids = invoke(
                "bigquery-list-table-ids", {"project": config["project"], "dataset": dataset}
            )
            require(
                isinstance(ids, list) and len(ids) <= 100 and len(set(ids)) == len(ids),
                "Invalid bounded table catalog.",
                code="invalid_provider_response",
            )
        else:
            ids = [
                resource_id.split(".")[2]
                for resource_id in selection
                if resource_id.split(".")[1] == dataset
            ]
        for table in ids:
            require(
                isinstance(table, str) and re.fullmatch(IDENTIFIER, table),
                "Unsupported schema identifier.",
                code="invalid_provider_response",
            )
            tables_seen += 1
            require(
                tables_seen <= 100, "Schema exceeds 100-table budget.", code="response_too_large"
            )
            metadata_result = invoke(
                "bigquery-get-table-info",
                {"project": config["project"], "dataset": dataset, "table": table},
            )
            require(
                isinstance(metadata_result, list)
                and len(metadata_result) == 1
                and isinstance(metadata_result[0], dict),
                "Invalid table metadata.",
                code="invalid_provider_response",
            )
            metadata = metadata_result[0]
            # BigQuery's official Go TableMetadata marshals exported field names.
            # External tables and views cannot be queried by our native SQL path.
            if metadata.get("Type") != "TABLE" or metadata.get("ExternalDataConfig"):
                continue
            columns = metadata.get("Schema")
            require(
                isinstance(columns, list), "Invalid table schema.", code="invalid_provider_response"
            )
            normalized = []
            for ordinal, column in enumerate(columns):
                require(
                    isinstance(column, dict)
                    and isinstance(column.get("Name"), str)
                    and re.fullmatch(IDENTIFIER, column["Name"]),
                    "Unsupported schema column.",
                    code="invalid_provider_response",
                )
                if selection is not None:
                    allowed_columns = selection[config["project"] + "." + dataset + "." + table]
                    if allowed_columns is not None and column["Name"] not in allowed_columns:
                        continue
                columns_seen += 1
                require(
                    columns_seen <= 1000,
                    "Schema exceeds 1000-column budget.",
                    code="response_too_large",
                )
                typ = {
                    "STRING": "string",
                    "INT64": "integer",
                    "FLOAT64": "number",
                    "BOOL": "boolean",
                    "BOOLEAN": "boolean",
                    "INTEGER": "integer",
                    "FLOAT": "number",
                    "RECORD": "object",
                    "JSON": "object",
                }.get(column.get("Type"), "string")
                normalized.append(
                    {
                        "name": column["Name"],
                        "data_type": column.get("Type"),
                        "type": column.get("Type"),
                        "source_type": column.get("Type"),
                        "value_type": "array" if column.get("Repeated") else typ,
                        "native_type": column.get("Type"),
                        "ordinal": ordinal,
                        "primary_key": False,
                        "default_value": None,
                        "metadata": {},
                        "nullable": not column.get("Required", False),
                    }
                )
            resources.append(
                {
                    "resource_id": config["project"] + "." + dataset + "." + table,
                    "display_name": table,
                    "name": table,
                    "selectable": True,
                    "resource_type": "table",
                    "columns": normalized,
                    "indexes": [],
                    "constraints": {},
                    "metadata": {},
                }
            )
            require(
                len(json.dumps(resources).encode()) <= max_bytes,
                "Schema exceeds byte budget.",
                code="response_too_large",
            )
    snapshot = {
        "schema_version": "dataset_schema_snapshot.v1",
        "source_kind": "bigquery",
        "resources": resources,
        "metadata": {"connector_id": "bigquery", "resource_count": len(resources)},
    }
    return {"schema_snapshot": snapshot, "resources": resources, "resource_count": len(resources)}
