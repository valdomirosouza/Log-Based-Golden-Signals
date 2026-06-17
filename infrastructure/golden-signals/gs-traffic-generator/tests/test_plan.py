"""Determinism / scenario / path-count tests for gs-traffic-generator (ADR-0086).

Verifies seeded reproducibility, the >=5-path guarantee (AC-03), and that the
latency-burst / error-burst scenarios actually shift their target signal so they
can trip the service's HITL thresholds (AC-09). Synthetic IPs only — the test
also asserts no routable/real IP shape leaks. Issue #25 (B-11).
"""

from __future__ import annotations

import ipaddress

import pytest
from gs_traffic_generator.config import DEFAULT_PATHS, GeneratorConfig, Scenario
from gs_traffic_generator.plan import (
    SyntheticRequest,
    build_plan,
    distinct_paths,
    error_fraction,
)


def _plan(scenario: Scenario = Scenario.STEADY, seed: int = 1337, total: int = 300):
    return build_plan(seed=seed, scenario=scenario, total_requests=total, paths=DEFAULT_PATHS)


# -- determinism -------------------------------------------------------------


def test_same_seed_same_plan() -> None:
    a = _plan(seed=42)
    b = _plan(seed=42)
    assert a == b


def test_different_seed_differs() -> None:
    a = _plan(seed=1)
    b = _plan(seed=2)
    assert a != b


def test_plan_does_not_touch_global_rng() -> None:
    import random

    random.seed(0)
    before = random.random()
    random.seed(0)
    _plan(seed=99)  # must not consume the global RNG
    after = random.random()
    assert before == after


# -- >=5 paths (AC-03) -------------------------------------------------------


def test_at_least_five_distinct_paths() -> None:
    plan = _plan(total=50)
    assert len(distinct_paths(plan)) >= 5


def test_every_configured_path_is_exercised() -> None:
    plan = _plan(total=len(DEFAULT_PATHS) * 3)
    assert distinct_paths(plan) == set(DEFAULT_PATHS)


def test_fewer_than_five_paths_rejected() -> None:
    with pytest.raises(ValueError, match=">= 5 paths"):
        build_plan(seed=1, scenario=Scenario.STEADY, total_requests=10, paths=("/a", "/b"))


# -- scenarios ---------------------------------------------------------------


def test_error_burst_raises_error_fraction() -> None:
    steady = error_fraction(_plan(Scenario.STEADY, total=300))
    burst = error_fraction(_plan(Scenario.ERROR_BURST, total=300))
    assert burst > steady
    assert burst >= 0.3  # the middle-third burst is all 5xx


def test_latency_burst_has_high_latency_samples() -> None:
    steady_max = max(r.latency_hint_ms for r in _plan(Scenario.STEADY, total=300))
    burst_max = max(r.latency_hint_ms for r in _plan(Scenario.LATENCY_BURST, total=300))
    assert burst_max > steady_max
    assert burst_max >= 1500  # clearly above a typical p99 HITL threshold


def test_steady_is_low_error() -> None:
    assert error_fraction(_plan(Scenario.STEADY, total=500)) < 0.1


# -- synthetic-PII safety (CLAUDE.md §3.1) -----------------------------------


def test_all_client_ips_are_documentation_ranges() -> None:
    plan = _plan(total=200)
    doc_nets = [
        ipaddress.ip_network("192.0.2.0/24"),
        ipaddress.ip_network("198.51.100.0/24"),
        ipaddress.ip_network("203.0.113.0/24"),
    ]
    for req in plan:
        ip = ipaddress.ip_address(req.client_ip)
        assert any(ip in net for net in doc_nets), f"non-synthetic IP leaked: {req.client_ip}"


# -- config ------------------------------------------------------------------


def test_config_rejects_unknown_scenario(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GS_DEMO_SCENARIO", "chaos")
    with pytest.raises(ValueError, match="invalid"):
        GeneratorConfig.from_env()


def test_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GS_DEMO_SCENARIO", "GS_DEMO_SEED", "GS_DEMO_TOTAL_REQUESTS", "GS_DEMO_PATHS"):
        monkeypatch.delenv(var, raising=False)
    cfg = GeneratorConfig.from_env()
    assert cfg.scenario is Scenario.STEADY
    assert len(cfg.paths) >= 5


def test_config_custom_paths_below_five_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GS_DEMO_PATHS", "/a,/b,/c")
    with pytest.raises(ValueError, match=">= 5 paths"):
        GeneratorConfig.from_env()


def test_empty_plan_error_fraction_zero() -> None:
    assert error_fraction([]) == 0.0


def test_negative_total_rejected() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        build_plan(seed=1, scenario=Scenario.STEADY, total_requests=-1, paths=DEFAULT_PATHS)


def test_synthetic_request_shape() -> None:
    plan = _plan(total=5)
    assert all(isinstance(r, SyntheticRequest) for r in plan)
    assert all(r.method == "GET" for r in plan)
