"""Enforce the security floor against both actual shipped Go executables."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("native_build_info", ROOT / "scripts/native_build_info.py")
assert SPEC is not None and SPEC.loader is not None
NATIVE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NATIVE)


def release_tuple(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+incompatible)?", value)
    assert match, f"non-release dependency version: {value}"
    return (*map(int, match.groups()[:3]), match[4] is None, match[4] or "")


@pytest.mark.parametrize("target", ["linux-amd64", "linux-arm64"])
def test_shipped_binary_dependency_security_floor(target):
    floor = json.loads((ROOT / "scripts/security-floor.json").read_text())
    version, modules, raw = NATIVE.build_info(ROOT / "bin" / target / "toolbox")
    previous_module = None
    for line in raw.splitlines():
        fields = line.split("\t")
        if fields[0] == "dep":
            previous_module = fields[1]
        elif fields[0] == "=>" and previous_module in floor["modules"]:
            raise AssertionError(f"security-sensitive module has an unmodeled replacement: {previous_module}")
    for module, minimum in floor["modules"].items():
        if module in modules:
            assert release_tuple(modules[module]) >= release_tuple(minimum), (module, modules[module], minimum)
    info = json.loads((ROOT / "toolbox-provenance.json").read_text())["binaries"][target]
    assert info["path"] == f"bin/{target}/toolbox"
    assert info["go_version"] == version
    assert info["modules"] == modules
    assert info["build_info"] == raw


@pytest.mark.parametrize("target", ["linux-amd64", "linux-arm64"])
def test_shipped_binary_go_security_floor(target):
    version, _, _ = NATIVE.build_info(ROOT / "bin" / target / "toolbox")
    assert version == "go1.27.4", version


def test_native_dependency_baseline_digest():
    baseline = ROOT / "scripts/patches/0000-security-baseline.patch"
    assert hashlib.sha256(baseline.read_bytes()).hexdigest() == (
        "958ad7a46b015c6520d1c60037c4e6fab41e756d417a618c612f08ac8806dfe7"
    )
    info = json.loads((ROOT / "toolbox-provenance.json").read_text())
    assert info["security_baseline"]["sha256"] == hashlib.sha256(baseline.read_bytes()).hexdigest()


@pytest.mark.parametrize("version", ["go1.26.2", "go1.27.0", "go1.27.1", "go1.27.3"])
def test_security_floor_rejects_unreviewed_compiler(version):
    with pytest.raises(ValueError, match="compiler"):
        NATIVE.check_security_floor(version, {"golang.org/x/net": "v0.62.0"}, "")
