"""Admit an actual ZIP and dispatch saved SQL with only cloud transport replaced."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
import os
import subprocess  # nosec B404 - test helper runs the admitted fixed entrypoint.
import sys
import tempfile

from core.application.extensions.archive_extraction_service import (
    ExtensionArchiveExtractionService,
    safe_extension_archive_file_mode,
)
from core.application.extensions.archive_validation_service import ExtensionArchiveValidationService
from core.application.extensions.command_core import ExtensionCommandCore
from core.application.extensions.lifecycle import validate_extension_descriptor_file
from core.infrastructure.extension_runtime.extension_runtime import run_extension_action

# BigQuery validates saved SQL by executing a bounded read. Keep the packaged
# entrypoint/parser real; replace only unavailable Google I/O in its child.
CLOUD_FIXTURE = """import os, socket, sys
sys.path.insert(0, os.getcwd())

def no_network(*args, **kwargs):
    raise AssertionError("Cloud network is forbidden in the dispatch regression")
socket.socket.connect = no_network
socket.socket.connect_ex = no_network
socket.create_connection = no_network
socket.getaddrinfo = no_network
import toolbox

def invoke(config, secrets, command, values, **kwargs):
    assert command["type"] == "bigquery-sql"
    assert config["write_mode"] == "blocked"
    assert config["tables"] == ["test-project.workflow.people"]
    assert secrets == {"access_token": "test-token"}
    assert command["statement"] == "SELECT name FROM `test-project.workflow.people` WHERE age > @minimum LIMIT 2"
    assert command["parameters"] == [{"name": "minimum", "type": "integer", "description": "Typed workflow parameter."}]
    assert values == {"minimum": 21}
    assert kwargs == {"timeout_seconds": 3}
    return [{"rows": [{"name": "Ada"}], "job_id": "local-fixture", "total_rows": 1, "truncated": False}]
toolbox.invoke = invoke
"""


def main(archive_path):
    unused: Any = None
    context: Any = SimpleNamespace()
    projection: Any = SimpleNamespace(
        extension_version_from_payload=lambda payload: payload["version"]
    )
    command = ExtensionCommandCore(
        context=unused, install_release=unused, cleanup_release_artifacts=lambda _: None
    )
    context.command_service = lambda _: command
    validator = ExtensionArchiveValidationService(context=context, projection=projection)
    upload = validator.validate_upload(
        filename=archive_path.name,
        content=archive_path.read_bytes(),
        max_archive_bytes=50 * 1024 * 1024,
    )
    extractor = ExtensionArchiveExtractionService(file_mode=safe_extension_archive_file_mode)
    with (
        extractor.extract(upload=upload, max_uncompressed_bytes=150 * 1024 * 1024) as extracted,
        tempfile.TemporaryDirectory(prefix="bigquery-cloud-fixture-") as fixture,
    ):
        descriptor_path = next(extracted.rglob("extension-descriptor.yaml"))
        descriptor = validate_extension_descriptor_file(descriptor_path)
        assert descriptor["valid"], descriptor["errors"]
        bundle = validator.validate_extracted_bundle(extracted_root=extracted, bl=None, scope=None)
        Path(fixture, "sitecustomize.py").write_text(CLOUD_FIXTURE)
        original_popen = subprocess.Popen

        def launch(command, *args, **kwargs):
            env = dict(kwargs["env"])
            env["PYTHONPATH"] = fixture + os.pathsep + env.get("PYTHONPATH", "")
            return original_popen(command, *args, **{**kwargs, "env": env})

        payload = {
            "connection_config": {
                "project": "test-project",
                "location": "US",
                "allowed_datasets": ["workflow"],
            },
            "secret_refs": {"values": {"access_token": "test-token"}},
            "dataset": {
                "dataset_id": "dispatch-dataset",
                "resource_scope_jsonb": {
                    "resources": [
                        {"resource_id": "test-project.workflow.people", "columns": ["name", "age"]}
                    ]
                },
            },
            "query_template": {
                "query_template_id": "dispatch-query",
                "dataset_id": "dispatch-dataset",
                "query_language": "sql",
                "query_definition_jsonb": {
                    "sql": "SELECT name FROM `test-project.workflow.people` WHERE age > :minimum"
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
            "limits": {"max_preview_rows": 1, "max_runtime_rows": 2, "timeout_seconds": 3},
        }
        with patch.object(subprocess, "Popen", side_effect=launch):
            invocation = run_extension_action(
                bundle.compiled,
                action_id="validate_query",
                input_payload=payload,
                timeout_seconds=5,
                runtime_context={"extension_install_id": "dispatch-install"},
            )
            assert invocation["status"] == "succeeded", invocation
            response = invocation["response"]
            assert response["ok"] is True, response
            assert response["result"] == {
                "valid": True,
                "read_only": True,
                "referenced_resources": ["test-project.workflow.people"],
                "rows": [{"name": "Ada"}],
                "row_count": 1,
                "output_schema": {"type": "object", "properties": {"name": {"type": "string"}}},
                "truncated": False,
            }
            for action_id in (
                "test_connection",
                "introspect_schema",
                "preview_query",
                "execute_template",
                "infer_output_schema",
            ):
                rejected = run_extension_action(
                    bundle.compiled,
                    action_id=action_id,
                    input_payload={"connection_config": {}, "secret_refs": {}},
                    timeout_seconds=5,
                    runtime_context={"extension_install_id": "dispatch-install"},
                )
                assert rejected["response"].get("error_code") == "invalid_connection", rejected
    print("Core packaged SQL validate_query succeeded; all six Dataset host actions admitted")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
