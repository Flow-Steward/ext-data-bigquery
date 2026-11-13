"""The CI builder accepts only a clean checkout of its pinned upstream source."""
import os
from pathlib import Path
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("extra", ["untracked", "ignored"])
@pytest.mark.parametrize("override", ["TOOLBOX_SOURCE", "FS_TOOLBOX_SOURCE_DIR"])
def test_builder_rejects_unreviewed_upstream_source(tmp_path, extra, override):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "fixture"], check=True, capture_output=True)
    commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    script = (ROOT / "scripts/build_toolbox.sh").read_text()
    import re
    script = re.sub(r"TOOLBOX_COMMIT=[0-9a-f]{40}", "TOOLBOX_COMMIT=" + commit, script)
    builder = tmp_path / "build_toolbox.sh"
    builder.write_text(script)
    if extra == "ignored":
        (source / ".git/info/exclude").write_text("unreviewed.go\n")
    (source / "unreviewed.go").write_text("package main")
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\necho 'docker must not run' >&2\nexit 86\n")
    docker.chmod(0o755)
    result = subprocess.run(["sh", str(builder)], env={**os.environ, override: str(source), "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]}, capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert "docker must not run" not in result.stderr
    assert "clean" in result.stderr.lower(), result.stderr
