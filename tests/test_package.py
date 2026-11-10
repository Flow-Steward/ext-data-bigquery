"""Release archive boundaries and native package contract."""

import hashlib
import importlib.util
import json
import stat
import shutil
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/package_extension.py"


def packager():
    assert SCRIPT.is_file(), "self-contained package helper must exist"
    spec = importlib.util.spec_from_file_location("package_extension", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "bigquery"
    root.mkdir()
    for name in (
        "extension.yaml",
        "extension-descriptor.yaml",
        "LICENSE",
        "NOTICE",
        "main.py",
    ):
        (root / name).write_text("release fixture\n")
    records = {}
    for target, machine in (("linux-amd64", 62), ("linux-arm64", 183)):
        path = root / "bin" / target / "toolbox"
        path.parent.mkdir(parents=True)
        helper_spec = importlib.util.spec_from_file_location("fixture_native_info", ROOT / "scripts/native_build_info.py")
        helper = importlib.util.module_from_spec(helper_spec)
        helper_spec.loader.exec_module(helper)
        version, modules, raw = helper.build_info(ROOT / "bin" / target / "toolbox")
        def encoded(value):
            data = value if isinstance(value, bytes) else value.encode()
            length = len(data)
            prefix = bytearray()
            while length >= 128:
                prefix.append((length & 127) | 128)
                length >>= 7
            prefix.append(length)
            return bytes(prefix) + data
        data = bytearray(64)
        data[:6] = b"\x7fELF\x02\x01"
        data[18:20] = machine.to_bytes(2, "little")
        data.extend(b"\xff Go buildinf:" + bytes([8, 2]) + bytes(16))
        data.extend(encoded(version))
        data.extend(encoded(bytes(16) + raw.encode() + bytes(16)))
        path.write_bytes(data)
        path.chmod(0o755)
        records[target] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "path": f"bin/{target}/toolbox",
            "go_version": version,
            "modules": modules,
            "build_info": raw,
        }
    provenance = json.loads((ROOT / "toolbox-provenance.json").read_text())
    provenance["binaries"] = records
    (root / "toolbox-provenance.json").write_text(json.dumps(provenance))
    patch = Path("scripts/patches/0000-security-baseline.patch")
    (root / patch).parent.mkdir(parents=True)
    shutil.copyfile(ROOT / patch, root / patch)
    approved = Path("scripts/patches/0000-approved-prebuilt-profiles.patch")
    shutil.copyfile(ROOT / approved, root / approved)
    for name in ("native_build_info.py", "package_extension.py", "security-floor.json"):
        shutil.copyfile(ROOT / "scripts" / name, root / "scripts" / name)
    return root


@pytest.mark.parametrize("target", ["linux-amd64", "linux-arm64"])
def test_archive_is_reproducible_and_contains_one_declared_target(source, tmp_path, target):
    module = packager()
    for name in (
        ".git",
        ".venv",
        ".fs-python",
        "dev-wheels",
        "__pycache__",
        ".pytest_cache",
        "dist",
    ):
        (source / name).mkdir()
        (source / name / "secret").write_text("exclude")
    for name in (
        "live-bigquery.local.json",
        ".fs-package.yaml",
        ".fs-extension-install.yaml",
    ):
        (source / name).write_text("exclude")
    (source / "outside-link").symlink_to(tmp_path / "secret")
    first = module.package_extension(source, target, tmp_path / "one")
    second = module.package_extension(source, target, tmp_path / "two")
    assert first.name == f"flowsteward.bigquery-1.1.4-{target}.zip"
    assert first.read_bytes() == second.read_bytes()
    prefix = "flowsteward_bigquery/"
    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
        assert all(name.startswith(prefix) for name in names)
        assert sorted(name for name in names if "/bin/" in name) == [
            f"{prefix}bin/{target}/toolbox"
        ]
        assert not any(
            "secret" in name or "local.json" in name or "outside-link" in name for name in names
        )
        assert f"{prefix}.fs-extension-install.yaml" not in names
        metadata = json.loads(archive.read(f"{prefix}.fs-package.yaml"))
        data = archive.read(f"{prefix}bin/{target}/toolbox")
        assert metadata == {
            "runtime_target": target,
            "native_executable": {
                "path": f"bin/{target}/toolbox",
                "sha256": hashlib.sha256(data).hexdigest(),
            },
        }
        assert (
            stat.S_IMODE(archive.getinfo(f"{prefix}bin/{target}/toolbox").external_attr >> 16)
            == 0o755
        )
        assert all(
            archive.getinfo(f"{prefix}{name}")
            for name in ("LICENSE", "NOTICE", "toolbox-provenance.json")
        )


@pytest.mark.parametrize("problem", ["hash", "size", "machine", "mode", "symlink", "extra"])
def test_refuses_unverifiable_native_payload(source, tmp_path, problem):
    module = packager()
    binary = source / "bin/linux-amd64/toolbox"
    if problem in {"hash", "size"}:
        provenance = json.loads((source / "toolbox-provenance.json").read_text())
        provenance["binaries"]["linux-amd64"]["sha256" if problem == "hash" else "size"] = (
            "0" * 64 if problem == "hash" else 1
        )
        (source / "toolbox-provenance.json").write_text(json.dumps(provenance))
    elif problem == "machine":
        data = bytearray(binary.read_bytes())
        data[18:20] = (183).to_bytes(2, "little")
        binary.write_bytes(data)
    elif problem == "mode":
        binary.chmod(0o644)
    elif problem == "symlink":
        binary.unlink()
        binary.symlink_to(source / "bin/linux-arm64/toolbox")
    else:
        (binary.parent / "extra").write_bytes(binary.read_bytes())
    with pytest.raises(ValueError):
        module.package_extension(source, "linux-amd64", tmp_path / "out")
    assert not list((tmp_path / "out").glob("*.zip"))


def test_installer_size_limits_leave_no_partial_archive(source, tmp_path, monkeypatch):
    module = packager()
    monkeypatch.setattr(module, "MAX_UNCOMPRESSED_BYTES", 1)
    with pytest.raises(ValueError, match="size limit"):
        module.package_extension(source, "linux-amd64", tmp_path / "out")
    assert not list((tmp_path / "out").glob("*.zip"))


@pytest.mark.parametrize("target", ["linux-amd64", "linux-arm64"])
def test_actual_release_native_archive_roundtrip(tmp_path, target):
    module = packager()
    archive_path = module.package_extension(ROOT, target, tmp_path / "one")
    second = module.package_extension(ROOT, target, tmp_path / "two")
    assert archive_path.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(archive_path) as archive:
        prefix = "flowsteward_bigquery/"
        assert (
            hashlib.sha256(archive.read(prefix + "LICENSE")).hexdigest()
            == "58d1e17ffe5109a7ae296caafcadfdbe6a7d176f0bc4ab01e12a689b0499d8bd"
        )
        manifest = json.loads(archive.read(prefix + "extension.yaml"))
        actions = json.loads(archive.read(prefix + "ui/actions/actions.yaml"))["actions"]
        declared = {action["action_id"]: action for action in actions}
        host_ids = {
            "test_connection",
            "introspect_schema",
            "validate_query",
            "preview_query",
            "execute_template",
            "infer_output_schema",
        }
        assert len(declared) == len(actions)
        assert host_ids <= declared.keys()
        assert host_ids.isdisjoint(
            effect["operation_id"] for effect in manifest["external_effects"]
        )
        for action_id in host_ids:
            action = declared[action_id]
            assert action["workflow_visible"] is False
            assert action["mutates_platform"] is False
            assert action["handler"] == {"mode": "extension"}
            assert {parameter["name"] for parameter in action["parameters"]} == {
                "connection_config",
                "secret_refs",
                "dataset",
                "query_template",
                "params",
                "limits",
            }
        metadata = json.loads(archive.read(prefix + ".fs-package.yaml"))
        binary_path = metadata["native_executable"]["path"]
        payload = archive.read(prefix + binary_path)
        assert hashlib.sha256(payload).hexdigest() == metadata["native_executable"]["sha256"]
        assert (
            int.from_bytes(payload[18:20], "little")
            == {"linux-amd64": 62, "linux-arm64": 183}[target]
        )
        assert sum(info.file_size for info in archive.infolist()) <= 150 * 1024 * 1024
        assert archive_path.stat().st_size <= 50 * 1024 * 1024
        assert [name for name in archive.namelist() if "/bin/" in name] == [prefix + binary_path]
        for required in (
            "LICENSE",
            "NOTICE",
            "scripts/build_toolbox.sh",
            "scripts/patches/0001-flowsteward-bigquery-safety.patch",
            "toolbox-provenance.json",
        ):
            assert prefix + required in archive.namelist()


def test_compressed_size_limit_leaves_no_partial_archive(source, tmp_path, monkeypatch):
    module = packager()
    monkeypatch.setattr(module, "MAX_ARCHIVE_BYTES", 1)
    with pytest.raises(ValueError, match="size limit"):
        module.package_extension(source, "linux-amd64", tmp_path / "out")
    assert list((tmp_path / "out").iterdir()) == []


def test_custom_output_inside_source_does_not_include_previous_archives(source):
    module = packager()
    out_dir = source / "release-output"
    first = module.package_extension(source, "linux-amd64", out_dir).read_bytes()
    second = module.package_extension(source, "linux-amd64", out_dir).read_bytes()
    assert first == second


@pytest.mark.parametrize("directory", ["bin/linux-amd64", "contracts"])
def test_output_must_not_overlap_release_payload(source, directory):
    module = packager()
    (source / "contracts").mkdir(exist_ok=True)
    (source / "contracts/connection_types.yaml").write_text("types: []\n")
    with pytest.raises(ValueError, match="Output directory"):
        module.package_extension(source, "linux-amd64", source / directory)
    assert not list((source / directory).glob("*.zip"))


def test_packaged_sql_dataset_query_dispatches_through_actual_core(tmp_path):
    """Missing host-action declarations must fail before the provider subprocess."""
    import os
    import platform
    import subprocess

    target = {"aarch64": "linux-arm64", "arm64": "linux-arm64", "x86_64": "linux-amd64"}[
        platform.machine().lower()
    ]
    archive = packager().package_extension(ROOT, target, tmp_path)
    result = subprocess.run(
        ["/usr/local/bin/python", str(ROOT / "scripts/validate_dataset_dispatch.py"), str(archive)],
        env={**os.environ, "PYTHONPATH": "/app", "FS_STACK_ENVIRONMENT": "development"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("version", ["go1.26.2", "go1.27.0", "go1.27.1"])
def test_package_command_rejects_unreviewed_runtime_with_truthful_provenance(source, tmp_path, version):
    import subprocess
    import sys
    binary = source / "bin/linux-amd64/toolbox"
    data = binary.read_bytes()
    offset = data.index(b"\xff Go buildinf:") + 32
    length = data[offset]
    replacement = version.encode()
    data = data[:offset] + bytes([len(replacement)]) + replacement + data[offset + 1 + length:]
    binary.write_bytes(data)
    info = json.loads((source / "toolbox-provenance.json").read_text())
    info["binaries"]["linux-amd64"].update({
        "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "go_version": version,
    })
    (source / "toolbox-provenance.json").write_text(json.dumps(info))
    result = subprocess.run([
        sys.executable, str(source / "scripts/package_extension.py"),
        "--runtime-target", "linux-amd64", "--out-dir", str(tmp_path / "rejected"),
    ], capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert "compiler is outside the reviewed security baseline" in result.stderr
    assert not list((tmp_path / "rejected").glob("*.zip"))


def test_packager_rejects_old_linked_module_with_truthful_provenance(source, tmp_path):
    binary = source / "bin/linux-amd64/toolbox"
    data = binary.read_bytes()
    old = b"dep\tgolang.org/x/net\tv0.62.0\t"
    new = b"dep\tgolang.org/x/net\tv0.53.0\t"
    assert old in data
    data = data.replace(old, new)
    binary.write_bytes(data)
    info = json.loads((source / "toolbox-provenance.json").read_text())
    entry = info["binaries"]["linux-amd64"]
    entry.update({"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    entry["modules"]["golang.org/x/net"] = "v0.53.0"
    entry["build_info"] = entry["build_info"].replace(old.decode(), new.decode())
    (source / "toolbox-provenance.json").write_text(json.dumps(info))
    with pytest.raises(ValueError, match="dependency is below the security baseline"):
        packager().package_extension(source, "linux-amd64", tmp_path / "rejected")
    assert not list((tmp_path / "rejected").glob("*.zip"))
