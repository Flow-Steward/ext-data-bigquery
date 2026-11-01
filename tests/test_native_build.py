"""Both pinned native payloads and patch provenance are required for publication."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_native_provenance():
    info = json.loads((ROOT / "toolbox-provenance.json").read_text())
    assert info["commit"] == "b0010060a939455d91e4f8ba62dc3e72b1892469"
    assert len(info["packages"]) == 2
    assert hashlib.sha256((ROOT / info["patch"]).read_bytes()).hexdigest() == info["patch_sha256"]
    for target, machine in [("linux-amd64", 62), ("linux-arm64", 183)]:
        binary = ROOT / "bin" / target / "toolbox"
        data = binary.read_bytes()
        assert data[:4] == b"\x7fELF"
        assert int.from_bytes(data[18:20], "little") == machine
        assert hashlib.sha256(data).hexdigest() == info["binaries"][target]["sha256"]


def test_native_help_without_cloud():
    import platform
    import subprocess

    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "amd64"
    result = subprocess.run(
        [str(ROOT / "bin" / ("linux-" + arch) / "toolbox"), "--help"],
        capture_output=True,
        timeout=10,
        env={"LANG": "C.UTF-8"},
        check=True,
    )
    assert b"Usage" in result.stdout
    assert result.stderr == b""
