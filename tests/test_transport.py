"""Managed credential lifetime and native configuration contract."""

import json
import os
import stat
import tempfile
import toolbox


def test_managed_token_is_environment_only():
    config = {
        "project": "test-project",
        "location": "US",
        "allowed_datasets": ["workflow"],
        "maximum_bytes_billed": 10485760,
        "write_mode": "blocked",
    }
    secret = {"access_token": "do-not-print-token"}
    docs = toolbox.managed_config(
        config, secret, {"type": "bigquery-sql", "statement": "SELECT 1", "parameters": []}, {}
    )
    assert "do-not-print-token" not in docs
    assert json.loads(docs.split("\n---\n")[0])["writeMode"] == "blocked"
    with tempfile.TemporaryDirectory() as directory:
        assert toolbox.managed_credentials(directory, config, secret) == {
            "LANG": "C.UTF-8",
            "FS_BIGQUERY_ACCESS_TOKEN": "do-not-print-token",
        }


def test_managed_adc_file():
    with tempfile.TemporaryDirectory() as directory:
        env = toolbox.managed_credentials(directory, {}, {"service_account_json": "{}"})
        path = env["GOOGLE_APPLICATION_CREDENTIALS"]
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert open(path).read() == "{}"
