"""Entrypoint for gs-traffic-generator (ADR-0086).

Builds the deterministic plan, then fires each request at the HAProxy listener
(fire-and-measure — it does not consume `/analytics`). The synthetic client IP
and a latency hint are passed as request headers so a canned upstream can shape
its response; HAProxy logs the genuine access line regardless.

The HTTP send is isolated here so the plan logic stays pure and unit-tested
without a network. Refs SPEC-LGS-002 §7, ADR-0086, issue #25 (B-11).
"""

from __future__ import annotations

import json
import sys
import time

import httpx

from .config import GeneratorConfig
from .plan import build_plan, distinct_paths


def _log(event: str, **fields: object) -> None:
    record = {"ts": int(time.time() * 1000), "component": "gs-traffic-generator", "event": event}
    record.update(fields)
    sys.stdout.write(json.dumps(record, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def run(config: GeneratorConfig, client: httpx.Client) -> int:
    """Issue the planned synthetic load at HAProxy; return the count sent."""
    plan = build_plan(
        seed=config.seed,
        scenario=config.scenario,
        total_requests=config.total_requests,
        paths=config.paths,
    )
    _log(
        "plan_built",
        scenario=config.scenario.value,
        seed=config.seed,
        total=len(plan),
        distinct_paths=len(distinct_paths(plan)),
    )
    sent = 0
    for req in plan:
        url = config.target_url.rstrip("/") + req.path
        try:
            client.request(
                req.method,
                url,
                headers={
                    # Synthetic-only IP for the canned upstream; HAProxy still
                    # logs %ci from the real connection. No real PII (§3.1).
                    "X-Synthetic-Client-Ip": req.client_ip,
                    "X-Latency-Hint-Ms": str(req.latency_hint_ms),
                    "X-Expected-Status": str(req.expected_status),
                },
                timeout=10.0,
            )
            sent += 1
        except httpx.HTTPError:
            # Fire-and-measure: a transport error is just an unsent request.
            _log("request_error", path=req.path)
    _log("run_complete", sent=sent)
    return sent


def main() -> int:
    config = GeneratorConfig.from_env()
    with httpx.Client() as client:
        run(config, client)
    return 0


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    raise SystemExit(main())
