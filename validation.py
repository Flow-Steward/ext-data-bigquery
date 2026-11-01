"""Closed bounded BigQuery connection validation."""

from __future__ import annotations
import json
import re
from typing import cast


class Failure(Exception):
    def __init__(self, code, message, *, dispatched=False):
        super().__init__(message)
        self.code, self.dispatched = code, dispatched


def require(condition, message, *, code="invalid_payload"):
    if not condition:
        raise Failure(code, message)


def text(value, name, maximum=65536, *, nonempty=False):
    require(isinstance(value, str), f"{name} must be text.")
    require(len(value.encode()) <= maximum and "\0" not in value, f"Invalid {name}.")
    require(not nonempty or bool(value.strip()), f"{name} is required.")
    return value


def integer(value, name, low, high):
    require(type(value) is int and low <= value <= high, f"Invalid {name}.")
    return value


def bounded_json(value, limit=256 * 1024):
    require(len(json.dumps(value, allow_nan=False).encode()) <= limit, "JSON byte limit exceeded.")


def connection_values(connection):
    try:
        config, secrets = connection.get("config") or {}, connection.get("secrets") or {}
        require(
            isinstance(config, dict) and isinstance(secrets, dict), "Connection objects required."
        )
        require(
            set(config)
            <= {
                "project",
                "location",
                "allowed_datasets",
                "maximum_bytes_billed",
                "request_timeout_seconds",
            },
            "Unknown connection field.",
        )
        require(
            set(secrets) <= {"service_account_json", "access_token"}, "Unknown credential field."
        )
        project = text(config.get("project"), "project", 63, nonempty=True)
        require(re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", project), "Invalid project.")
        location = text(config.get("location"), "location", 64, nonempty=True)
        require(re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,63}", location), "Invalid location.")
        datasets = config.get("allowed_datasets")
        require(
            isinstance(datasets, list) and 1 <= len(datasets) <= 32,
            "Explicit allowed datasets required.",
        )
        datasets = cast(list[str], datasets)
        for dataset in datasets:
            require(
                isinstance(dataset, str)
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,1023}", dataset),
                "Invalid dataset.",
            )
        require(len(set(datasets)) == len(datasets), "Duplicate dataset.")
        require(
            bool(secrets.get("access_token")) != bool(secrets.get("service_account_json")),
            "Choose exactly one credential.",
        )
        if secrets.get("access_token"):
            token = text(secrets["access_token"], "access_token", 16384, nonempty=True)
            require(not any(c.isspace() for c in token), "Invalid access token.")
        else:
            raw = text(
                secrets["service_account_json"], "service_account_json", 65536, nonempty=True
            )
            sa = json.loads(raw)
            require(
                isinstance(sa, dict)
                and sa.get("type") == "service_account"
                and sa.get("project_id") == project,
                "A matching service account is required.",
            )
            require(
                sa.get("token_uri") == "https://oauth2.googleapis.com/token",
                "Only the fixed Google OAuth endpoint is supported.",
            )
            require(
                set(sa)
                <= {
                    "type",
                    "project_id",
                    "private_key_id",
                    "private_key",
                    "client_email",
                    "client_id",
                    "auth_uri",
                    "token_uri",
                    "auth_provider_x509_cert_url",
                    "client_x509_cert_url",
                    "universe_domain",
                },
                "Unknown credential fields.",
            )
            require(
                sa.get("universe_domain", "googleapis.com") == "googleapis.com",
                "Only Google Cloud is supported.",
            )
            require(
                isinstance(sa.get("private_key"), str) and isinstance(sa.get("client_email"), str),
                "Incomplete service account.",
            )
        return {
            "project": project,
            "location": location,
            "allowed_datasets": datasets,
            "maximum_bytes_billed": integer(
                config.get("maximum_bytes_billed", 10485760),
                "maximum_bytes_billed",
                1,
                1000000000000,
            ),
            "request_timeout_seconds": integer(
                config.get("request_timeout_seconds", 20), "request_timeout_seconds", 1, 30
            ),
        }, secrets
    except (Failure, ValueError, TypeError, UnicodeError) as exc:
        raise Failure(
            "invalid_connection", "Invalid BigQuery project connection or credentials."
        ) from exc
