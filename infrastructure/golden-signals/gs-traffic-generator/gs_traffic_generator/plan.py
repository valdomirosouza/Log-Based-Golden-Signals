"""Deterministic synthetic-request plan for gs-traffic-generator (ADR-0086).

A :func:`build_plan` call is pure and seeded: the same
``(seed, scenario, total_requests, paths)`` always yields the identical request
sequence, which is what makes AC-02/AC-03/AC-04/AC-09 reproducible (ADR-0086 §1).

Scenarios (ADR-0086 §3):
* ``steady`` — balanced multi-path traffic, ~nominal latency, low error rate.
* ``latency-burst`` — a contiguous burst of high server-response-time requests
  intended to push the service's p99 latency past its HITL threshold (AC-09).
* ``error-burst`` — a contiguous burst of ``>= 500`` responses intended to push
  the error rate past the service's HITL threshold (AC-09).

The generator never lowers a threshold or flips a flag — it only *reaches* the
existing thresholds with real load (ADR-0086 §3, CLAUDE.md §3.3). Client IPs are
drawn from the synthetic TEST-NET ranges only — no real PII (CLAUDE.md §3.1).
Refs #18, #25 (B-11).
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

from .config import Scenario

# RFC 5737 / RFC 3849 documentation-only ranges — never routable, never real PII.
_TEST_NET_PREFIXES = ("192.0.2.", "198.51.100.", "203.0.113.")

# Burst occupies the middle third of the run so before/after windows exist.
_BURST_FRACTION = 1.0 / 3.0


@dataclass(frozen=True)
class SyntheticRequest:
    """One planned request to issue at the HAProxy listener."""

    method: str
    path: str
    expected_status: int
    # Hint for a canned-latency upstream / the request's intended delay (ms).
    latency_hint_ms: int
    client_ip: str


def _synthetic_ip(rng: random.Random) -> str:
    prefix = rng.choice(_TEST_NET_PREFIXES)
    return prefix + str(rng.randint(1, 254))


def build_plan(
    *,
    seed: int,
    scenario: Scenario,
    total_requests: int,
    paths: Sequence[str],
) -> list[SyntheticRequest]:
    """Build the deterministic request plan for one run.

    Determinism is guaranteed by seeding a private ``random.Random`` instance
    (never the global RNG), so concurrent generators with different seeds do not
    interfere and a fixed seed reproduces byte-identically.
    """
    if total_requests < 0:
        raise ValueError("total_requests must be >= 0")
    if len(paths) < 5:
        raise ValueError("plan requires >= 5 paths (ENV-FR-07 / AC-03)")

    # Deterministic synthetic load only — NOT a security/crypto context. A
    # seeded Mersenne Twister is exactly what reproducibility (ADR-0086 §1)
    # requires; a CSPRNG would be both unseedable-for-repro and wrong here.
    rng = random.Random(seed)  # noqa: S311 - synthetic demo traffic, not crypto
    plan: list[SyntheticRequest] = []

    burst_len = int(total_requests * _BURST_FRACTION)
    burst_start = (total_requests - burst_len) // 2
    burst_end = burst_start + burst_len

    for i in range(total_requests):
        path = paths[i % len(paths)]  # round-robin guarantees every path appears
        in_burst = burst_start <= i < burst_end

        method = "GET"
        status = 200
        latency = rng.randint(5, 50)

        if scenario is Scenario.STEADY:
            # ~2% baseline errors, nominal latency.
            if rng.random() < 0.02:
                status = 500
        elif scenario is Scenario.LATENCY_BURST:
            if in_burst:
                latency = rng.randint(1500, 3000)  # > typical p99 HITL threshold
            elif rng.random() < 0.02:
                status = 500
        elif scenario is Scenario.ERROR_BURST:
            if in_burst:
                status = 500  # drive error_rate past the HITL threshold
            elif rng.random() < 0.02:
                status = 500

        plan.append(
            SyntheticRequest(
                method=method,
                path=path,
                expected_status=status,
                latency_hint_ms=latency,
                client_ip=_synthetic_ip(rng),
            )
        )

    return plan


def distinct_paths(plan: Sequence[SyntheticRequest]) -> set[str]:
    """Return the set of distinct paths a plan exercises (AC-03 helper)."""
    return {req.path for req in plan}


def error_fraction(plan: Sequence[SyntheticRequest]) -> float:
    """Fraction of planned requests with an error (>=400) status."""
    if not plan:
        return 0.0
    errors = sum(1 for r in plan if r.expected_status >= 400)
    return errors / len(plan)
