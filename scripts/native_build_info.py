"""Read Go build information without executing target binaries; record shipped modules."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path


def build_info(binary: Path) -> tuple[str, dict[str, str], str]:
    data = binary.read_bytes()
    start = data.index(b"\xff Go buildinf:")
    if data[start + 14] not in (4, 8) or data[start + 15] & 2 == 0:
        raise ValueError("Go inline build information required")
    offset = start + 32
    def read_string() -> bytes:
        nonlocal offset
        length = shift = 0
        while True:
            value = data[offset]
            offset += 1
            length |= (value & 127) << shift
            if value < 128:
                break
            shift += 7
            if shift >= 64:
                raise ValueError("invalid Go build information length")
        value = data[offset:offset + length]
        if len(value) != length:
            raise ValueError("truncated Go build information")
        offset += length
        return value
    version = read_string().decode("utf-8")
    raw = read_string()
    # Go surrounds module information with a 16-byte framing sentinel.
    raw = raw[16:-16].decode("utf-8")
    modules = {}
    for line in raw.splitlines():
        fields = line.split("\t")
        if len(fields) >= 3 and fields[0] == "dep":
            modules[fields[1]] = fields[2]
    if not modules:
        raise ValueError("shipped binary has no module information")
    return version, modules, raw


def record(root: Path) -> None:
    path = root / "toolbox-provenance.json"
    info = json.loads(path.read_text())
    info["security_baseline"] = {
        "patch": "scripts/patches/0000-security-baseline.patch",
        "sha256": hashlib.sha256((root / "scripts/patches/0000-security-baseline.patch").read_bytes()).hexdigest(),
    }
    for target in ("linux-amd64", "linux-arm64"):
        binary = root / "bin" / target / "toolbox"
        version, modules, raw = build_info(binary)
        info["binaries"][target].update({
            "path": binary.relative_to(root).as_posix(),
            "go_version": version,
            "modules": modules,
            "build_info": raw,
        })
    path.write_text(json.dumps(info, indent=2) + "\n")
    (root / "SHA256SUMS").write_text("".join(
        entry["sha256"] + "  " + entry["path"] + "\n"
        for entry in info["binaries"].values()
    ))


def check_security_floor(version: str, modules: dict[str, str], raw: str) -> None:
    """Accept only the reviewed compiler and effective selected dependency versions."""
    import re
    if version != "go1.26.10":
        raise ValueError(f"native compiler is outside the reviewed security baseline: {version}")
    floors = json.loads((Path(__file__).resolve().parent / "security-floor.json").read_text())["modules"]
    previous = None
    for line in raw.splitlines():
        fields = line.split("\t")
        if fields[0] == "dep":
            previous = fields[1]
        elif fields[0] == "=>" and previous in floors:
            raise ValueError(f"security-sensitive module has an unmodeled replacement: {previous}")
    def release(value):
        match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?", value)
        if not match:
            raise ValueError(f"unsupported native dependency version: {value}")
        return (*map(int, match.groups()[:3]), match[4] is None, match[4] or "")
    for name, minimum in floors.items():
        if name in modules and release(modules[name]) < release(minimum):
            raise ValueError(f"native dependency is below the security baseline: {name} {modules[name]} < {minimum}")


def verify_security(root: Path, target: str, provenance: dict) -> None:
    binary = root / "bin" / target / "toolbox"
    version, modules, raw = build_info(binary)
    check_security_floor(version, modules, raw)
    entry = provenance["binaries"][target]
    expected = {"path": f"bin/{target}/toolbox", "go_version": version, "modules": modules, "build_info": raw}
    if any(entry.get(key) != value for key, value in expected.items()):
        raise ValueError("native build information does not match provenance")
    if provenance.get("commit") != "b0010060a939455d91e4f8ba62dc3e72b1892469":
        raise ValueError("native source commit does not match the reviewed baseline")
    if provenance.get("go_image") != "golang:1.26.10-bookworm@sha256:5de7c99eb78ca5e515a4c340235d0955ec5964a5071c492c15503d7c267da473":
        raise ValueError("native build image does not match the reviewed baseline")
    baseline = provenance.get("security_baseline", {})
    digest = "a6623600324d2bdec1b30102e749678d4fea326bbb9d9f4f586a7ba472d395e2"
    if baseline != {"patch": "scripts/patches/0000-security-baseline.patch", "sha256": digest}:
        raise ValueError("native dependency baseline does not match provenance")
    path = root / baseline["patch"]
    if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("native dependency baseline patch is missing or changed")


if __name__ == "__main__":
    record(Path(__file__).resolve().parents[1])
