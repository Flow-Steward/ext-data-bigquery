"""Public contract parity and fixed cloud transport declaration."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads((ROOT / path).read_text())


def test_parity():
    m = load("extension.yaml")
    d = load("extension-descriptor.yaml")
    assert m["extension_id"] == d["extension_id"] == "flowsteward.bigquery"
    assert m["runtime"] == d["runtime"]
    assert {x["operation_id"] for x in m["external_effects"]} == {"write"}
    ops = load("contracts/operation_manifest.yaml")["operations"]
    acts = load("ui/actions/actions.yaml")["actions"]
    forms = load("contracts/step_ui_manifest.yaml")["forms"]
    assert (
        {x["operation_id"] for x in ops if x["workflow_visible"]}
        == {x["action_id"] for x in acts}
        == {x["operation_id"] for x in forms}
        == {"query", "write"}
    )
    for op in (op for op in ops if op["workflow_visible"]):
        act = next(x for x in acts if x["action_id"] == op["operation_id"])
        assert {x["name"] for x in op["inputs"]} == {x["name"] for x in act["parameters"]}
    connection = load("contracts/connection_types.yaml")["connection_types"][0]
    assert connection["config_schema"]["additionalProperties"] is False
    assert connection["secret_schema"]["additionalProperties"] is False
    assert connection["capabilities"]["workflow_trigger"] is False
    assert set(m["egress"]["hosts"]) == {
        "bigquery.googleapis.com",
        "oauth2.googleapis.com",
        "www.googleapis.com",
    }


def test_canonical_apache_license():
    import hashlib

    assert (
        hashlib.sha256((ROOT / "LICENSE").read_bytes()).hexdigest()
        == "58d1e17ffe5109a7ae296caafcadfdbe6a7d176f0bc4ab01e12a689b0499d8bd"
    )
