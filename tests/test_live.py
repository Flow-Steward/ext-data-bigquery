"""Explicit live integration opt-in; never claims a mock as cloud evidence."""

import json
import os
from pathlib import Path
import pytest
import main


@pytest.mark.skipif(
    not os.environ.get("FS_BIGQUERY_LIVE_ENVELOPE"),
    reason="No opt-in Google Cloud envelope; live BigQuery unverified.",
)
def test_live_select():
    # Operator supplies a dedicated least-privilege project connection envelope.
    payload = json.loads(Path(os.environ["FS_BIGQUERY_LIVE_ENVELOPE"]).read_text())
    assert payload["action"]["action_id"] == "query"
    assert main.handle(payload)["ok"]
