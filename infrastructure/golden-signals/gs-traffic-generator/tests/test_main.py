"""Run-loop tests for gs-traffic-generator main (ADR-0086). Issue #25 (B-11).

Drives `run()` with a fake httpx client so the fire-and-measure loop, URL
assembly, header shaping, and error handling are covered without a network.
"""

from __future__ import annotations

import json

import httpx
import pytest
from gs_traffic_generator.config import DEFAULT_PATHS, GeneratorConfig, Scenario
from gs_traffic_generator.main import run


def _config(total: int = 12, scenario: Scenario = Scenario.STEADY) -> GeneratorConfig:
    return GeneratorConfig(
        target_url="http://haproxy:8080/",
        scenario=scenario,
        seed=7,
        total_requests=total,
        paths=DEFAULT_PATHS,
    )


class FakeClient:
    def __init__(self, raise_on=None) -> None:
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self._raise_on = raise_on

    def request(self, method, url, headers, timeout):
        if self._raise_on is not None and url.endswith(self._raise_on):
            raise httpx.ConnectError("refused")
        self.requests.append((method, url, headers))


def test_run_issues_every_planned_request(capsys: pytest.CaptureFixture[str]) -> None:
    client = FakeClient()
    sent = run(_config(total=12), client)  # type: ignore[arg-type]
    assert sent == 12
    assert len(client.requests) == 12
    # URL assembly: no double slash, path appended to target.
    assert all(url.startswith("http://haproxy:8080/api/") for _m, url, _h in client.requests)


def test_run_sets_synthetic_headers_only(capsys: pytest.CaptureFixture[str]) -> None:
    client = FakeClient()
    run(_config(total=6), client)  # type: ignore[arg-type]
    _m, _url, headers = client.requests[0]
    assert "X-Synthetic-Client-Ip" in headers
    assert "X-Latency-Hint-Ms" in headers
    assert "X-Expected-Status" in headers


def test_run_tolerates_transport_error() -> None:
    client = FakeClient(raise_on="/api/orders")
    sent = run(_config(total=12), client)  # type: ignore[arg-type]
    # /api/orders requests fail; the rest still send (fire-and-measure).
    assert 0 < sent < 12


def test_run_emits_structured_logs(capsys: pytest.CaptureFixture[str]) -> None:
    client = FakeClient()
    run(_config(total=6), client)  # type: ignore[arg-type]
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    kinds = {e["event"] for e in events}
    assert {"plan_built", "run_complete"} <= kinds
    assert all(e["component"] == "gs-traffic-generator" for e in events)
