"""Cloud-free host-v2 BigQuery contract; all provider calls are mocked."""

from copy import deepcopy
import pytest
import main


def envelope(action="query", **args):
    return {
        "contract_version": "extension_host_v2",
        "mode": "action",
        "runtime_context": {
            "group_id": None,
            "for_each_id": None,
            "step_id": "s",
            "step_execution_id": "e",
            "account_id": "a",
            "project_id": "p",
            "external_effect_id": "claim",
        },
        "action": {
            "action_id": action,
            "input": {
                "connection_ref": "c",
                "statement": "SELECT @x AS x",
                "parameters": (
                    [{"name": "x", "type": "STRING", "value": "hello"}] if action == "query" else []
                ),
                **args,
            },
            "target": {
                "connection": {
                    "connection_id": "c",
                    "connection_type": "bigquery",
                    "account_id": "a",
                    "project_id": "p",
                    "config": {
                        "project": "test-project",
                        "location": "US",
                        "allowed_datasets": ["workflow"],
                    },
                    "secrets": {"access_token": "test-token"},
                }
            },
        },
    }


@pytest.fixture
def calls(monkeypatch):
    out = []

    def invoke(*a, **kw):
        out.append(deepcopy((a, kw)))
        return [
            {
                "rows": [{"x": "hello"}],
                "job_id": "job",
                "project": "test-project",
                "location": "US",
                "statement_type": "SELECT",
                "total_rows": 1,
                "truncated": False,
            }
        ]

    monkeypatch.setattr(main.toolbox, "invoke", invoke)
    return out


def test_query(calls):
    result = main.handle(envelope())
    assert result["ok"], result
    command, args = calls[0][0][2:]
    assert command["type"] == "bigquery-sql"
    assert command["parameters"][0]["type"] == "string"
    assert args == {"x": "hello"}
    assert result["result"]["job_id"] == "job"


@pytest.mark.parametrize(
    "args",
    [
        {"statement": "SELECT 1; DELETE FROM `test-project.workflow.t` WHERE TRUE"},
        {"statement": "CREATE TABLE t AS SELECT 1"},
        {"statement": "SELECT EXTERNAL_QUERY('x','x')"},
        {"statement": "SELECT 1", "rogue": 1},
        {"parameters": [{"name": "x", "type": "INT64", "value": 9007199254740993}]},
        {"parameters": [{"name": "x", "type": "FLOAT64", "value": float("inf")}]},
    ],
)
def test_rejects_before_io(calls, args):
    assert not main.handle(envelope(**args))["ok"]
    assert not calls


def test_write_suppressed(calls):
    p = envelope("write", statement="DELETE FROM `test-project.workflow.t` WHERE TRUE")
    p["runtime_context"]["test_mode"] = True
    assert main.handle(p)["error_code"] == "test_mode_suppressed"
    assert not calls


def test_missing_claim(calls):
    p = envelope("write", statement="DELETE FROM `test-project.workflow.t` WHERE TRUE")
    p["runtime_context"].pop("external_effect_id")
    assert main.handle(p)["error_code"] == "effect_claim_required"
    assert not calls


def test_scope_mismatch(calls):
    p = envelope()
    p["action"]["target"]["connection"]["project_id"] = "other"
    assert main.handle(p)["error_code"] == "invalid_connection"
    assert not calls


def test_write_uncertain(monkeypatch):
    from validation import Failure

    def fail(*a, **kw):
        raise Failure("timeout", "Deadline.", dispatched=True)

    monkeypatch.setattr(main.toolbox, "invoke", fail)
    p = envelope("write", statement="DELETE FROM `test-project.workflow.t` WHERE TRUE")
    result = main.handle(p)
    assert result["external_effect_status"] == "timeout_unknown"
    assert result["retryable"] is False
    assert result["job_id"].startswith("fs_")


def test_effect_job_stable(calls):
    p = envelope("write", statement="DELETE FROM `test-project.workflow.t` WHERE TRUE")
    main.handle(p)
    main.handle(p)
    assert calls[0][0][0]["job_id"] == calls[1][0][0]["job_id"]


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO `test-project.workflow.t` (x) VALUES (@x)",
        "UPDATE `test-project.workflow.t` SET x = @x WHERE TRUE",
        "MERGE `test-project.workflow.t` t USING `test-project.workflow.s` s ON t.x = s.x WHEN MATCHED THEN UPDATE SET x = @x",
    ],
)
def test_dml_compiles(calls, statement):
    result = main.handle(
        envelope(
            "write",
            statement=statement,
            parameters=[{"name": "x", "type": "STRING", "value": "hello"}],
        )
    )
    assert result["ok"], result
    assert calls[0][0][0]["write_mode"] == "allowed"
    assert "test-project.workflow.t" in calls[0][0][0]["tables"]


@pytest.mark.parametrize(
    "action, statement",
    [
        ("query", "SELECT * FROM other_project.forbidden.table"),
        ("write", "DELETE FROM other_project.forbidden.table WHERE TRUE"),
        ("write", "INSERT other_project.forbidden.table (x) VALUES (1)"),
        ("write", "DELETE other_project.forbidden.table WHERE TRUE"),
    ],
)
def test_requires_explicit_fully_qualified_physical_tables(calls, action, statement):
    result = main.handle(envelope(action, statement=statement, parameters=[]))
    assert not result["ok"]
    assert not calls


def test_rejects_incomplete_table_metadata_threshold(calls):
    statement = "SELECT 1 FROM " + " CROSS JOIN ".join(
        "`test-project.workflow.t` AS t" + str(i) for i in range(50)
    )
    result = main.handle(envelope(statement=statement, parameters=[]))
    assert not result["ok"]
    assert not calls


def test_rejects_comma_join_targets(calls):
    result = main.handle(
        envelope(
            statement="SELECT 1 FROM `test-project.workflow.t` t, other.forbidden t2", parameters=[]
        )
    )
    assert not result["ok"]
    assert not calls


def test_function_commas_remain_supported(calls):
    result = main.handle(
        envelope(statement="SELECT COALESCE(@x, 'fallback') AS x FROM `test-project.workflow.t`")
    )
    assert result["ok"], result
