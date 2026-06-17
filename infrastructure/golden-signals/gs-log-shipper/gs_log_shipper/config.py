"""Env-driven configuration for gs-log-shipper (ENV-NFR-02: config-via-env).

Every tunable is an environment variable with a documented default in
`.env.example`; the shipper starts on defaults alone except for the required
`GS_API_KEYS`. No secret is ever hard-coded here (CLAUDE.md §3.2).

Refs: SPEC-LGS-002 §8/§9.1, ADR-0084. Issue #24 (B-10).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class ShipperConfig:
    """Resolved shipper configuration.

    Attributes mirror the `.env.example` GS_SHIPPER_* surface. ``api_key`` is
    read from ``GS_API_KEYS`` (required) and is *never* logged.
    """

    ingestion_url: str
    api_key: str
    batch_max_entries: int
    batch_max_seconds: float
    max_retries: int
    backoff_base_seconds: float
    backoff_max_seconds: float
    max_line_bytes: int
    request_timeout_seconds: float
    syslog_host: str
    syslog_port: int

    @classmethod
    def from_env(cls) -> ShipperConfig:
        """Build config from the process environment.

        ``GS_API_KEYS`` is required (ENV-FR-09). The shipper sends the *first*
        comma-separated key as its ``X-API-Key`` credential; the service holds
        the full allow-list. A missing/empty value is a fail-fast config error.
        """
        raw_keys = os.environ.get("GS_API_KEYS", "").strip()
        first_key = raw_keys.split(",")[0].strip() if raw_keys else ""
        if not first_key:
            raise ValueError(
                "GS_API_KEYS is required (ENV-FR-09) — set it in the environment; "
                "never commit it to the tree."
            )
        return cls(
            ingestion_url=os.environ.get(
                "GS_INGESTION_URL", "http://golden-signals:8085/ingestion"
            ),
            api_key=first_key,
            batch_max_entries=_int("GS_SHIPPER_BATCH_MAX_ENTRIES", 100),
            batch_max_seconds=_float("GS_SHIPPER_BATCH_MAX_SECONDS", 2.0),
            max_retries=_int("GS_SHIPPER_MAX_RETRIES", 3),
            backoff_base_seconds=_float("GS_SHIPPER_BACKOFF_BASE_SECONDS", 0.5),
            backoff_max_seconds=_float("GS_SHIPPER_BACKOFF_MAX_SECONDS", 30.0),
            max_line_bytes=_int("GS_SHIPPER_MAX_LINE_BYTES", 8192),
            request_timeout_seconds=_float("GS_SHIPPER_REQUEST_TIMEOUT_SECONDS", 10.0),
            # Syslog listener bind (ADR-0084 Amendment 2026-06-17, ENV-FR-12).
            # Confined to gs-net (never host-published); HAProxy reaches it by
            # the `gs-log-shipper` service name on the isolated bridge.
            # 0.0.0.0 default is safe: the container sits on the isolated gs-net
            # only and is never host-published (ENV-FR-12 / ADR-0085 §1).
            syslog_host=os.environ.get("GS_SHIPPER_SYSLOG_HOST", "0.0.0.0"),  # noqa: S104
            syslog_port=_int("GS_SHIPPER_SYSLOG_PORT", 514),
        )
