"""Bounded managed Toolbox subprocess; no shell or caller executable overrides."""

from __future__ import annotations
import contextlib
import hashlib
import json
import os
from pathlib import Path
import platform
import selectors
import signal
import subprocess  # nosec B404
import tempfile
import time
from validation import Failure, require

ROOT = Path(__file__).resolve().parent
MAX_OUTPUT = 1024 * 1024


def _binary():
    arch = {
        "aarch64": "arm64",
        "arm64": "arm64",
        "x86_64": "amd64",
        "amd64": "amd64",
    }.get(platform.machine().lower())
    if platform.system() != "Linux" or not arch:
        raise Failure("native_unavailable", "Toolbox requires Linux AMD64 or ARM64.")
    target = "linux-" + arch
    path = ROOT / "bin" / target / "toolbox"
    try:
        expected = json.loads((ROOT / "toolbox-provenance.json").read_text())["binaries"][target][
            "sha256"
        ]
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        require(
            actual == expected,
            "Packaged Toolbox checksum does not match.",
            code="native_unavailable",
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Failure(
            "native_unavailable",
            "Install the packaged Toolbox binary for this architecture.",
        ) from exc
    return str(path)


def managed_config(config, secrets, command, arguments, ip=None):
    source = {
        "kind": "source",
        "name": "bq-source",
        "type": "bigquery",
        "project": config["project"],
        "location": config["location"],
        "writeMode": config["write_mode"],
        "allowedDatasets": config["allowed_datasets"],
        "maxQueryResultRows": 100,
        "maximumBytesBilled": config["maximum_bytes_billed"],
        "workflowJobID": config.get("job_id", ""),
        "workflowTables": config.get("tables", []),
    }
    tool = {
        "kind": "tool",
        "name": "bq-command",
        "source": "bq-source",
        "description": "One bounded BigQuery operation.",
        **command,
    }
    return (
        "\n---\n".join(
            json.dumps(d, ensure_ascii=True).replace("$", "\\u0024") for d in (source, tool)
        )
        + "\n"
    )


def managed_credentials(directory, config, secrets):
    env = {"LANG": "C.UTF-8"}
    if secrets.get("access_token"):
        env["FS_BIGQUERY_ACCESS_TOKEN"] = secrets["access_token"]
    else:
        path = Path(directory) / "credentials.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(secrets["service_account_json"])
        env["GOOGLE_APPLICATION_CREDENTIALS"] = str(path)
    return env


def _send(process, payload, deadline):
    if process.stdin is None:
        raise Failure("provider_unavailable", "Toolbox input is unavailable.")
    data = memoryview(json.dumps(payload, allow_nan=False, separators=(",", ":")).encode() + b"\n")
    require(len(data) <= 264 * 1024, "Toolbox input exceeds its byte limit.")
    fd = process.stdin.fileno()
    os.set_blocking(fd, False)
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_WRITE)
        while data:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise Failure("timeout", "Toolbox input deadline exceeded.")
            try:
                written = os.write(fd, data[:65536])
            except BlockingIOError:
                continue
            if written <= 0:
                raise Failure("provider_unavailable", "Toolbox input closed.")
            data = data[written:]


def _read(process, request_id, deadline, state):
    require(
        process.stdout is not None and process.stderr is not None,
        "Toolbox streams unavailable.",
        code="provider_unavailable",
    )
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Failure("timeout", "Toolbox deadline exceeded.")
            events = selector.select(remaining)
            if not events:
                raise Failure("timeout", "Toolbox deadline exceeded.")
            for key, _ in events:
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                state["bytes"] += len(chunk)
                require(
                    state["bytes"] <= MAX_OUTPUT,
                    "Toolbox output exceeded its byte limit.",
                    code="response_too_large",
                )
                if key.data == "stderr":
                    continue
                state["buffer"].extend(chunk)
                while b"\n" in state["buffer"]:
                    line, _, tail = state["buffer"].partition(b"\n")
                    state["buffer"] = bytearray(tail)
                    try:
                        data = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if isinstance(data, dict) and data.get("id") == request_id:
                        require(
                            data.get("jsonrpc") == "2.0",
                            "Invalid Toolbox protocol.",
                            code="invalid_provider_response",
                        )
                        return data
    raise Failure("provider_unavailable", "Toolbox closed before its response.")


def _stop(process):
    if process.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def _result(response):
    if response.get("error"):
        raise Failure("provider_unavailable", "Toolbox rejected the command.")
    result = response.get("result")
    require(
        isinstance(result, dict),
        "Invalid Toolbox result.",
        code="invalid_provider_response",
    )
    if result.get("isError"):
        detail = str(result.get("content", ""))[:8192].lower()
        if "exceeds result byte limit" in detail:
            code = "response_too_large"
        elif "authentication" in detail or "unauthorized" in detail:
            code = "authentication_failed"
        else:
            code = "provider_unavailable"
        raise Failure(code, "BigQuery command failed.")
    content = result.get("content")
    require(
        isinstance(content, list),
        "Invalid Toolbox content.",
        code="invalid_provider_response",
    )
    values = []
    for block in content:
        require(
            isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str),
            "Invalid Toolbox content.",
            code="invalid_provider_response",
        )
        try:
            values.append(json.loads(block["text"]))
        except (ValueError, TypeError) as exc:
            raise Failure("invalid_provider_response", "Toolbox result is not JSON.") from exc
    return values


def invoke(config, secrets, command, arguments, *, timeout_seconds):
    executable = _binary()
    dispatched = False
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix="fs-bigquery-") as directory:
            path = Path(directory) / "tools.yaml"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(managed_config(config, secrets, command, arguments))
            process = subprocess.Popen(  # nosec B603
                [
                    executable,
                    "--config",
                    str(path),
                    "--stdio",
                    "--disable-reload",
                    "--log-level",
                    "ERROR",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=managed_credentials(directory, config, secrets),
                start_new_session=True,
            )
            deadline = time.monotonic() + timeout_seconds
            state = {"bytes": 0, "buffer": bytearray()}
            try:
                _send(
                    process,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {
                                "name": "flowsteward.bigquery",
                                "version": "1.0.0",
                            },
                        },
                    },
                    deadline,
                )
                initial = _read(process, 1, deadline, state)
                require(
                    not initial.get("error") and isinstance(initial.get("result"), dict),
                    "Toolbox initialization failed.",
                    code="provider_unavailable",
                )
                _send(
                    process,
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                        "params": {},
                    },
                    deadline,
                )
                dispatched = True
                _send(
                    process,
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "bq-command", "arguments": arguments},
                    },
                    deadline,
                )
                return _result(_read(process, 2, deadline, state))
            finally:
                _stop(process)
    except Failure as exc:
        exc.dispatched = exc.dispatched or dispatched
        raise
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        raise Failure(
            "provider_unavailable", "Toolbox process failed.", dispatched=dispatched
        ) from exc
