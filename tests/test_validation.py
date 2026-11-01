"""Connection and SQL boundaries reject unsafe configuration before native I/O."""

import json
import pytest
from test_runtime import envelope
import main
from validation import Failure, connection_values


def test_service_account_managed_contract():
    p = envelope()["action"]["target"]["connection"]
    p["secrets"] = {
        "service_account_json": json.dumps(
            {
                "type": "service_account",
                "project_id": "test-project",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_email": "identity@test-project.iam.gserviceaccount.com",
                "private_key": "not-a-real-key",
            }
        )
    }
    config, secrets = connection_values(p)
    assert config["maximum_bytes_billed"] == 10485760
    assert set(secrets) == {"service_account_json"}


@pytest.mark.parametrize(
    "config",
    [
        {"endpoint": "http://127.0.0.1"},
        {"maximum_bytes_billed": 0},
        {"maximum_bytes_billed": True},
        {"request_timeout_seconds": 31},
        {"allowed_datasets": []},
        {"allowed_datasets": ["workflow", "workflow"]},
        {"allowed_datasets": ["other.workflow"]},
        {"project": "../credentials"},
        {"location": "https://example.com"},
    ],
)
def test_bad_config(config):
    p = envelope()["action"]["target"]["connection"]
    p["config"].update(config)
    with pytest.raises(Failure, match="Invalid BigQuery"):
        connection_values(p)


@pytest.mark.parametrize(
    "secrets",
    [
        {},
        {"credentials_path": "/etc/passwd"},
        {"access_token": "x", "service_account_json": "{}"},
        {"access_token": "line\nbreak"},
        {
            "service_account_json": json.dumps(
                {"type": "external_account", "credential_source": {"url": "http://localhost"}}
            )
        },
        {
            "service_account_json": json.dumps(
                {
                    "type": "service_account",
                    "project_id": "test-project",
                    "token_uri": "https://evil.example/token",
                }
            )
        },
    ],
)
def test_bad_secrets(secrets):
    p = envelope()["action"]["target"]["connection"]
    p["secrets"] = secrets
    with pytest.raises(Failure, match="Invalid BigQuery"):
        connection_values(p)


@pytest.mark.parametrize("scope", ["account_id", "project_id"])
def test_missing_runtime_scope(scope, monkeypatch):
    p = envelope()
    p["runtime_context"].pop(scope)
    monkeypatch.setattr(
        main.toolbox, "invoke", lambda *a, **kw: pytest.fail("Unexpected provider I/O")
    )
    assert not main.handle(p)["ok"]
