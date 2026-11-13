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
    if version != "go1.27.4":
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
        match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+incompatible)?", value)
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
    if provenance.get("commit") != "45ed205cc8682ed066c82e0b65bcd0e8fc3becce":
        raise ValueError("native source commit does not match the reviewed baseline")
    if provenance.get("go_image") != "golang:1.27.3-bookworm@sha256:dc0963f32053e4bc451d24d87e3892d42d01021b61f093e98eb95c0352bb84b0":
        raise ValueError("native build image does not match the reviewed baseline")
    if (provenance.get("source"), provenance.get("upstream_tag"), provenance.get("upstream_commit")) != ("https://github.com/googleapis/mcp-toolbox", "v1.17.0", "45ed205cc8682ed066c82e0b65bcd0e8fc3becce"):
        raise ValueError("native upstream source identity does not match the reviewed baseline")
    profile = provenance.get("approved_profiles", {})
    profile_path = root / "scripts/patches/0000-approved-prebuilt-profiles.patch"
    profile_digest = "df264c60006ad1a01812b427fb6a9593115ddd86227fc9522b8bae6731616aca"
    if profile != {"patch": "scripts/patches/0000-approved-prebuilt-profiles.patch", "sha256": profile_digest} or not profile_path.is_file() or profile_path.is_symlink() or hashlib.sha256(profile_path.read_bytes()).hexdigest() != profile_digest:
        raise ValueError("native approved profile baseline is missing or changed")
    if provenance.get("go_image_role") != "bootstrap":
        raise ValueError("native container image role must be bootstrap")
    install = provenance.get("go_toolchain_install", {})
    helper_path = root / "scripts/install-go-toolchain.sh"
    sources_path = root / "scripts/go-toolchain-sources.json"
    selected_path = root / "scripts/go-toolchain-selected.json"
    helper_sha = "a12d9761e28980bf808a29e2898dcb9511391c1b70c06014ad49cbadfc8733c6"
    sources_sha = "5008b838d6855cb0d67f3100ce18a4335be04ad3b36012fcc95036e998da997e"
    if install.get("helper_path") != "scripts/install-go-toolchain.sh" or install.get("helper_sha256") != helper_sha or not helper_path.is_file() or helper_path.is_symlink() or hashlib.sha256(helper_path.read_bytes()).hexdigest() != helper_sha:
        raise ValueError("native compiler installer is missing or changed")
    if not sources_path.is_file() or sources_path.is_symlink() or hashlib.sha256(sources_path.read_bytes()).hexdigest() != sources_sha:
        raise ValueError("native compiler sources are missing or changed")
    sources = json.loads(sources_path.read_text())
    selected = install.get("selected_sdk", {})
    archive = sources.get("archives", {}).get(selected.get("host_arch"), {})
    if install.get("sources") != sources or selected.get("version") != "go1.27.4" or sources.get("version") != "go1.27.4" or not archive or selected.get("url") != archive.get("url") or selected.get("sha256") != archive.get("sha256") or set(selected) != {"version", "host_arch", "url", "sha256"}:
        raise ValueError("native selected compiler SDK does not match official sources")
    if not selected_path.is_file() or selected_path.is_symlink() or json.loads(selected_path.read_text()) != selected:
        raise ValueError("native selected compiler metadata is missing or changed")
    baseline = provenance.get("security_baseline", {})
    digest = "c9437f65a0b046d7bb2065f5f64b5f878e4341b1d7eedf3075d8afac5a186fa6"
    if baseline != {"patch": "scripts/patches/0000-security-baseline.patch", "sha256": digest}:
        raise ValueError("native dependency baseline does not match provenance")
    path = root / baseline["patch"]
    if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("native dependency baseline patch is missing or changed")


if __name__ == "__main__":
    record(Path(__file__).resolve().parents[1])
