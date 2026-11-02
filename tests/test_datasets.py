"""Canonical Dataset/Data Query requests, with cloud transport mocked only."""

from copy import deepcopy

import pytest

import main
from test_runtime import envelope


def dataset_request(action="execute_template", sql=None, **overrides):
    request = envelope(action)
    request["action"].pop("target")
    request["action"]["input"] = {
        "connection_config": {
            "project": "test-project",
            "location": "US",
            "allowed_datasets": ["workflow"],
        },
        "secret_refs": {"values": {"access_token": "test-token"}},
        "dataset": {
            "dataset_id": "d",
            "resource_scope_jsonb": {
                "resources": [
                    {"resource_id": "test-project.workflow.people", "columns": ["name", "age"]}
                ]
            },
        },
        "query_template": {
            "dataset_id": "d",
            "query_language": "sql",
            "query_definition_jsonb": {
                "sql": sql or "SELECT name FROM `test-project.workflow.people` WHERE age > :minimum"
            },
            "param_schema_jsonb": {
                "type": "object",
                "properties": {"minimum": {"type": "integer"}},
                "required": ["minimum"],
            },
            "output_schema_jsonb": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
            },
        },
        "params": {"minimum": 21},
        "limits": {"max_runtime_rows": 2, "max_preview_rows": 1, "timeout_seconds": 3},
        **overrides,
    }
    return request


@pytest.fixture
def provider(monkeypatch):
    calls = []

    def invoke(*args, **kwargs):
        calls.append(deepcopy((args, kwargs)))
        return [{"rows": [{"name": "Ada"}], "job_id": "job", "total_rows": 1, "truncated": False}]

    monkeypatch.setattr(main.toolbox, "invoke", invoke)
    return calls


@pytest.mark.parametrize(
    "action", ["execute_template", "preview_query", "validate_query", "infer_output_schema"]
)
def test_dataset_dispatch_and_native_bound_parameters(provider, action):
    result = main.handle(dataset_request(action))
    assert result["ok"], result
    config, secrets, command, values = provider[0][0]
    assert config["write_mode"] == "blocked"
    assert config["tables"] == ["test-project.workflow.people"]
    assert "age > @minimum" in command["statement"]
    assert command["parameters"][0]["type"] == "integer"
    assert values == {"minimum": 21}
    assert provider[0][1]["timeout_seconds"] == 3
    assert result["result"]["output_schema"]["properties"]["name"]["type"] == "string"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT secret FROM `test-project.workflow.people` WHERE age > :minimum",
        "SELECT * FROM `test-project.workflow.people` WHERE age > :minimum",
        "SELECT name FROM `test-project.workflow.other` WHERE age > :minimum",
        "SELECT name FROM `other-project.workflow.people` WHERE age > :minimum",
        "SELECT name FROM `test-project.workflow.people` UNION SELECT secret FROM `test-project.workflow.people`",
        "SELECT name FROM `test-project.workflow.people` WHERE age > (SELECT secret FROM `test-project.workflow.people`)",
        "SELECT CAST(secret AS STRING) FROM `test-project.workflow.people` WHERE age > :minimum",
        "SELECT `secret` FROM `test-project.workflow.people` WHERE age > :minimum",
        "SELECT name FROM `test-project.workflow.people` WHERE age > @minimum",
        "DELETE FROM `test-project.workflow.people` WHERE age > :minimum",
    ],
)
def test_scope_and_read_only_fail_closed(provider, sql):
    assert not main.handle(dataset_request(sql=sql))["ok"]
    assert not provider


def test_literal_parameter_text_is_not_rewritten(provider):
    result = main.handle(
        dataset_request(
            sql="SELECT name FROM `test-project.workflow.people` WHERE name = ':ignored' AND age > :minimum"
        )
    )
    assert result["ok"], result
    assert "':ignored'" in provider[0][0][2]["statement"]


def test_empty_results_keep_saved_output_schema(monkeypatch):
    monkeypatch.setattr(
        main.toolbox, "invoke", lambda *a, **k: [{"rows": [], "job_id": "empty", "total_rows": 0}]
    )
    result = main.handle(dataset_request())
    assert result["ok"], result
    assert result["result"]["rows"] == []
    assert "name" in result["result"]["output_schema"]["properties"]


@pytest.mark.parametrize("change", ["param", "scope", "dataset", "limit", "deadline"])
def test_bad_canonical_values_rejected(provider, change):
    request = dataset_request()
    args = request["action"]["input"]
    if change == "param":
        args["params"]["minimum"] = "21"
    if change == "scope":
        args["dataset"]["resource_scope_jsonb"] = {}
    if change == "dataset":
        args["query_template"]["dataset_id"] = "other"
    if change == "limit":
        args["limits"]["max_runtime_rows"] = 0
    if change == "deadline":
        args["limits"]["timeout_seconds"] = 0
    assert not main.handle(request)["ok"]
    assert not provider


def test_admin_catalog_and_health(provider):
    request = envelope()
    request["mode"] = "query"
    request.pop("action")
    for query in ("supported_connectors", "runtime_health"):
        request["query"] = {"query_id": query, "input": {}}
        result = main.handle(request)
        assert result["ok"], result
        if query == "supported_connectors":
            assert result["result"]["connectors"][0]["query_language"] == "sql"
    assert not provider


def test_schema_introspection_is_bounded_to_connection_datasets(monkeypatch):
    calls = []

    def invoke(*args, **kwargs):
        calls.append((args, kwargs))
        if args[2]["type"] == "bigquery-list-table-ids":
            return ["people"]
        return [
            {"Type": "TABLE", "Schema": [{"Name": "name", "Type": "STRING", "Required": False}]}
        ]

    monkeypatch.setattr(main.toolbox, "invoke", invoke)
    request = dataset_request("introspect_schema")
    request["action"]["input"].pop("dataset")  # Connection discovery precedes Dataset selection.
    result = main.handle(request)
    assert result["ok"], result
    resource = result["result"]["schema_snapshot"]["resources"][0]
    assert resource["resource_id"] == "test-project.workflow.people"
    assert resource["columns"][0]["name"] == "name"
    assert resource["columns"][0]["value_type"] == "string"
    assert result["result"]["schema_snapshot"]["schema_version"] == "dataset_schema_snapshot.v1"
    assert calls[0][0][2]["type"] == "bigquery-list-table-ids"
    assert calls[0][0][3] == {"project": "test-project", "dataset": "workflow"}
    assert calls[1][0][2]["type"] == "bigquery-get-table-info"
    assert calls[1][0][3] == {"project": "test-project", "dataset": "workflow", "table": "people"}


def test_dataset_registry_uses_existing_connection_schemas():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "extension.yaml").read_text())
    contracts = manifest["runtime"]["extension_contract_v2"]
    connector = contracts["dataset_connectors"][0]
    connection = json.loads((root / "contracts/connection_types.yaml").read_text())[
        "connection_types"
    ][0]
    assert connector["connection_schema"] == connection["config_schema"]
    assert connector["secret_schema"] == connection["secret_schema"]
    assert connector["query_language"] == "sql"
    assert connector["capabilities"]["execute_template"] is True
    assert "query" in manifest["features"]
    assert {item["query_id"] for item in contracts["resource_query_manifest"]} == {
        "runtime_health",
        "supported_connectors",
    }


def test_result_byte_budget_and_row_budget(provider):
    request = dataset_request()
    request["action"]["input"]["limits"]["max_result_bytes"] = 2
    assert not main.handle(request)["ok"]


def test_connection_test_calls_real_transport(provider):
    result = main.handle(dataset_request("test_connection"))
    assert result["ok"], result
    assert provider
    assert result["result"]["status"] == "healthy"


def test_dataset_crosses_real_managed_mcp_bridge(tmp_path, monkeypatch):
    """Replace only the unavailable Google provider with a local MCP fixture."""
    import sys

    fake = tmp_path / "provider"
    fake.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """import json, os, sys
path = sys.argv[sys.argv.index("--config") + 1]
source, tool = [json.loads(d) for d in open(path).read().split("\\n---\\n") if d.strip()]
assert source["writeMode"] == "blocked"
assert source["workflowTables"] == ["test-project.workflow.people"]
assert source["maximumBytesBilled"] == 10485760
assert tool["parameters"] == [{"name": "minimum", "type": "integer", "description": "Typed workflow parameter."}]
assert "age > @minimum" in tool["statement"]
assert "test-token" not in open(path).read()
assert os.environ["FS_BIGQUERY_ACCESS_TOKEN"] == "test-token"
for line in sys.stdin:
    request = json.loads(line)
    if request.get("id") == 1:
        print(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}), flush=True)
    if request.get("id") == 2:
        assert request["params"]["arguments"] == {"minimum": 21}
        result = {"rows": [], "job_id": "native-wire", "total_rows": 0}
        print(json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}}), flush=True)
"""
    )
    fake.chmod(0o755)
    monkeypatch.setattr(main.toolbox, "_binary", lambda: str(fake))
    result = main.handle(dataset_request())
    assert result["ok"], result
    assert result["result"]["rows"] == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT name FROM `test-project.workflow.people` WHERE secret = :minimum",
        "SELECT name FROM `test-project.workflow.people` ORDER BY secret",
        "SELECT name FROM `test-project.workflow.people` WHERE name = ':minimum'",
        "SELECT name FROM `test-project.workflow.people` WHERE age > :missing",
        "SELECT name FROM `test-project.workflow.people` WHERE age > :minimum -- comment",
        "SELECT name FROM `test-project.workflow.people`;",
        "SELECT name FROM `test-project.workflow.people` WHERE age > :minimum OR secret IS NULL",
        "SELECT name FROM `test-project.workflow.people` WHERE age IN (:minimum, secret)",
    ],
)
def test_scope_checks_every_predicate_and_sort_column(provider, sql):
    assert not main.handle(dataset_request(sql=sql))["ok"]
    assert not provider


def test_smaller_row_budget_and_truncation(monkeypatch):
    captured = []

    def invoke(*args, **kwargs):
        captured.append(args)
        return [{"rows": [{"name": "Ada"}, {"name": "Grace"}], "job_id": "job", "total_rows": 2}]

    monkeypatch.setattr(main.toolbox, "invoke", invoke)
    request = dataset_request("preview_query")
    result = main.handle(request)
    assert result["ok"], result
    assert result["result"]["rows"] == [{"name": "Ada"}]
    assert result["result"]["row_count"] == 1
    assert result["result"]["truncated"] is True
    assert captured[0][0]["max_result_rows"] == 1
    assert captured[0][2]["statement"].endswith("LIMIT 2")


def test_schema_catalog_overflow_does_not_publish_partial_snapshot(monkeypatch):
    monkeypatch.setattr(main.toolbox, "invoke", lambda *a, **k: ["t" + str(i) for i in range(101)])
    request = dataset_request("introspect_schema")
    request["action"]["input"].pop("dataset")  # Connection discovery precedes Dataset selection.
    result = main.handle(request)
    assert not result["ok"]
    assert "schema_snapshot" not in result.get("result", {})


def test_schema_skips_external_tables_and_views(monkeypatch):
    def invoke(config, secrets, command, values, **kwargs):
        if command["type"] == "bigquery-list-table-ids":
            return ["people", "view", "external"]
        return [
            {
                "Type": {"people": "TABLE", "view": "VIEW", "external": "EXTERNAL"}[
                    values["table"]
                ],
                "Schema": [{"Name": "name", "Type": "STRING"}],
            }
        ]

    monkeypatch.setattr(main.toolbox, "invoke", invoke)
    request = dataset_request("introspect_schema")
    request["action"]["input"].pop("dataset")  # Connection discovery precedes Dataset selection.
    result = main.handle(request)
    assert result["ok"], result
    assert [r["resource_id"] for r in result["result"]["resources"]] == [
        "test-project.workflow.people"
    ]


@pytest.mark.parametrize(
    "action", ["test_connection", "introspect_schema", "preview_query", "execute_template"]
)
def test_core_dataset_admin_null_workflow_scope(monkeypatch, action):
    """Dataset admin reads trust host-resolved config without workflow owners."""
    calls = []

    def invoke(config, secrets, command, values, **kwargs):
        calls.append(command["type"])
        if command["type"] == "bigquery-list-table-ids":
            return ["people"]
        if command["type"] == "bigquery-get-table-info":
            return [{"Type": "TABLE", "Schema": [{"Name": "name", "Type": "STRING"}]}]
        return [{"rows": [{"name": "Ada"}], "job_id": "job", "total_rows": 1}]

    monkeypatch.setattr(main.toolbox, "invoke", invoke)
    request = dataset_request(action)
    request["runtime_context"].update(
        {"account_id": None, "project_id": None, "source": "data_connection_admin"}
    )
    request["action"]["context"] = {"workspace_id": "workspace", "connection_id": "connection"}
    result = main.handle(request)
    assert result["ok"], result
    assert calls


@pytest.mark.parametrize("action", ["query", "write"])
def test_workflow_owners_stay_required_after_dataset_dispatch(provider, action):
    request = envelope(action)
    request["runtime_context"].update({"account_id": None, "project_id": None})
    result = main.handle(request)
    assert not result["ok"]
    assert "project-scoped account and project" in result["error"]
    assert not provider


@pytest.mark.parametrize("rows", [[{"age": 21}], []])
def test_infer_schema_uses_current_projection_over_stale_saved_output(monkeypatch, rows):
    monkeypatch.setattr(
        main.toolbox,
        "invoke",
        lambda *a, **k: [{"rows": rows, "job_id": "job", "total_rows": len(rows)}],
    )
    request = dataset_request(
        "infer_output_schema",
        sql="SELECT age FROM `test-project.workflow.people` WHERE age > :minimum",
    )
    request["action"]["input"]["dataset"]["schema_snapshot_jsonb"] = {
        "schema_version": "dataset_schema_snapshot.v1",
        "resources": [
            {
                "resource_id": "test-project.workflow.people",
                "columns": [
                    {"name": "age", "value_type": "integer"},
                    {"name": "name", "value_type": "string"},
                ],
            }
        ],
    }
    result = main.handle(request)
    assert result["ok"], result
    properties = result["result"]["output_schema"]["properties"]
    assert properties == {"age": {"type": "integer"}}


def test_selected_dataset_schema_does_not_expose_other_tables_or_columns(monkeypatch):
    fetched = []

    def invoke(config, secrets, command, values, **kwargs):
        if command["type"] == "bigquery-list-table-ids":
            return ["people", "private_table"]
        fetched.append(values["table"])
        return [
            {
                "Type": "TABLE",
                "Schema": [
                    {"Name": "name", "Type": "STRING"},
                    {"Name": "age", "Type": "INTEGER"},
                    {"Name": "secret", "Type": "STRING"},
                ],
            }
        ]

    monkeypatch.setattr(main.toolbox, "invoke", invoke)
    result = main.handle(dataset_request("introspect_schema"))
    assert result["ok"], result
    resources = result["result"]["schema_snapshot"]["resources"]
    assert [resource["resource_id"] for resource in resources] == ["test-project.workflow.people"]
    assert [column["name"] for column in resources[0]["columns"]] == ["name", "age"]
    assert fetched == ["people"]


def test_infer_empty_alias_preserves_current_source_column_type(monkeypatch):
    monkeypatch.setattr(
        main.toolbox, "invoke", lambda *a, **k: [{"rows": [], "job_id": "job", "total_rows": 0}]
    )
    request = dataset_request(
        "infer_output_schema",
        sql="SELECT age AS years FROM `test-project.workflow.people` WHERE age > :minimum",
    )
    request["action"]["input"]["dataset"]["schema_snapshot_jsonb"] = {
        "resources": [
            {
                "resource_id": "test-project.workflow.people",
                "columns": [{"name": "age", "value_type": "integer"}],
            }
        ]
    }
    result = main.handle(request)
    assert result["ok"], result
    assert result["result"]["output_schema"]["properties"] == {"years": {"type": "integer"}}


def test_existing_sql_row_object_output_schema_is_retained_for_empty_reads(monkeypatch):
    monkeypatch.setattr(
        main.toolbox, "invoke", lambda *a, **k: [{"rows": [], "job_id": "job", "total_rows": 0}]
    )
    request = dataset_request()
    saved = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "additionalProperties": False,
    }
    request["action"]["input"]["query_template"]["output_schema_jsonb"] = saved
    result = main.handle(request)
    assert result["ok"], result
    assert result["result"]["output_schema"] == saved


def test_inference_uses_existing_sql_per_row_object_contract(provider):
    result = main.handle(dataset_request("infer_output_schema"))
    assert result["ok"], result
    assert result["result"]["output_schema"] == {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "additionalProperties": True,
    }
