"""Env-driven configuration for gs-traffic-generator (ENV-NFR-02).

Every tunable is an env var with a documented default in `.env.example`; no
secret is held here (the generator hits HAProxy, which needs no API key —
ADR-0086 §2: it drives the listener, not `/ingestion`). Refs ADR-0086, #25.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum


class Scenario(StrEnum):
    """Selectable load profile (ADR-0086 §3)."""

    STEADY = "steady"
    LATENCY_BURST = "latency-burst"
    ERROR_BURST = "error-burst"


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# At least five distinct paths (AC-03 / ENV-FR-07). Kept as a stable default
# ordering so a given seed reproduces the same request sequence.
DEFAULT_PATHS: tuple[str, ...] = (
    "/api/orders",
    "/api/items",
    "/api/users",
    "/api/checkout",
    "/api/search",
    "/api/health",
)


@dataclass(frozen=True)
class GeneratorConfig:
    """Resolved generator configuration."""

    target_url: str
    scenario: Scenario
    seed: int
    total_requests: int
    paths: tuple[str, ...]

    @classmethod
    def from_env(cls) -> GeneratorConfig:
        """Build config from the environment, validating the scenario flag.

        An unrecognised ``GS_DEMO_SCENARIO`` is a fail-fast config error rather
        than a silent fallback (so a typo in a demo can't quietly run steady).
        """
        raw_scenario = os.environ.get("GS_DEMO_SCENARIO", Scenario.STEADY.value).strip()
        try:
            scenario = Scenario(raw_scenario)
        except ValueError as exc:
            valid = ", ".join(s.value for s in Scenario)
            raise ValueError(
                f"GS_DEMO_SCENARIO={raw_scenario!r} is invalid; expected one of: {valid}"
            ) from exc

        raw_paths = os.environ.get("GS_DEMO_PATHS", "").strip()
        paths = (
            tuple(p.strip() for p in raw_paths.split(",") if p.strip())
            if raw_paths
            else DEFAULT_PATHS
        )
        if len(paths) < 5:
            raise ValueError("GS_DEMO_PATHS must list >= 5 paths (ENV-FR-07 / AC-03)")

        return cls(
            target_url=os.environ.get("GS_DEMO_TARGET_URL", "http://haproxy:8080"),
            scenario=scenario,
            seed=_int("GS_DEMO_SEED", 1337),
            total_requests=_int("GS_DEMO_TOTAL_REQUESTS", 500),
            paths=paths,
        )
