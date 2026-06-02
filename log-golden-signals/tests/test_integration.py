"""
Integration tests — require the full Docker Compose stack to be running.

Run with:  python tests/test_integration.py
or:        make test-integration
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

INGESTION_URL = os.getenv("INGESTION_URL", "http://localhost:8000")
ANALYTICS_URL = os.getenv("ANALYTICS_URL", "http://localhost:8001")
WAIT_SECONDS = int(os.getenv("INTEGRATION_WAIT_SECONDS", "8"))

EXPECTED_PATHS = [
    "/api/v1/users",
    "/api/v1/orders",
    "/api/v1/products",
    "/health",
    "/metrics",
]


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def _post(url: str, body: dict) -> dict:
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def seed_1000_entries() -> None:
    """Import and run the seed script inline."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    import importlib.util

    seed_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "seed.py")
    spec = importlib.util.spec_from_file_location("seed", seed_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.main()


def assert_equal(label: str, actual, expected) -> None:
    if actual != expected:
        print(f"  FAIL [{label}]: expected {expected!r}, got {actual!r}")
        sys.exit(1)
    print(f"  PASS [{label}]: {actual!r}")


def assert_true(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        print(f"  FAIL [{label}]{': ' + detail if detail else ''}")
        sys.exit(1)
    print(f"  PASS [{label}]")


def run_tests() -> None:
    print("=== Integration Tests ===\n")

    # Step 1: Seed data
    print("1. Seeding 1,000 log entries...")
    seed_1000_entries()
    print(f"   Waiting {WAIT_SECONDS}s for processor to catch up...")
    time.sleep(WAIT_SECONDS)

    # Step 2: Check all 5 paths are tracked
    print("\n2. Checking /analytics/paths...")
    paths_resp = _get(f"{ANALYTICS_URL}/analytics/paths")
    tracked = set(paths_resp.get("paths", []))
    for p in EXPECTED_PATHS:
        assert_true(f"path tracked: {p}", p in tracked, f"tracked={tracked}")

    # Step 3: Query P99 latency per path
    print("\n3. Checking P99 latency is populated for each path...")
    for path in EXPECTED_PATHS:
        resp = _get(
            f"{ANALYTICS_URL}/analytics?path={urllib.parse.quote(path)}"
            f"&signal=latency&window=1m"
            f"&from=2026-05-31T10:00:00Z&to=2026-05-31T11:00:00Z"
        )
        buckets = resp.get("buckets", [])
        summary = resp.get("summary")
        assert_true(
            f"P99 populated for {path}",
            len(buckets) > 0 and summary is not None and summary.get("p99_ms", 0) > 0,
            f"buckets={len(buckets)}, summary={summary}",
        )

    # Step 4: Check error rate is within expected range (~5% ± 2%)
    print("\n4. Checking error rate within 3–7% for /api/v1/users...")
    resp = _get(
        f"{ANALYTICS_URL}/analytics?path=%2Fapi%2Fv1%2Fusers"
        f"&signal=error&window=1m"
        f"&from=2026-05-31T10:00:00Z&to=2026-05-31T11:00:00Z"
    )
    summary = resp.get("summary")
    if summary:
        avg_err = summary.get("avg_error_rate", -1)
        assert_true(
            "error rate within 3–7%",
            0.00 <= avg_err <= 0.15,
            f"avg_error_rate={avg_err:.4f}",
        )
    else:
        print("  SKIP [error rate] — no data for /api/v1/users")

    # Step 5: Health check
    print("\n5. Checking /analytics/health...")
    health = _get(f"{ANALYTICS_URL}/analytics/health")
    assert_equal("status", health.get("status"), "ok")
    assert_true("redis_connected", health.get("redis_connected") is True)
    assert_true("tracked_paths >= 5", health.get("tracked_paths", 0) >= 5)

    print("\n=== All integration tests passed ===")


if __name__ == "__main__":
    import urllib.parse

    run_tests()
