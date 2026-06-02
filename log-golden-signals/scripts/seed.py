#!/usr/bin/env python3
"""
Seed script — generates 1,000 synthetic HAProxy log entries and sends them
to the Ingestion API in batches of 100.

No framework dependencies: uses only stdlib.

Distribution:
  - 5 paths with weighted traffic
  - Latency: log-normal with μ=100ms, σ=50ms
  - Error rate: ~5% (mix of 4xx and 5xx)
  - Variable bytes_sent per path
"""

import json
import math
import os
import random
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

INGESTION_URL = os.getenv("INGESTION_URL", "http://localhost:8000")
BATCH_SIZE = 100
TOTAL = 1000
SEED = 42

PATHS = [
    "/api/v1/users",
    "/api/v1/orders",
    "/api/v1/products",
    "/health",
    "/metrics",
]
PATH_WEIGHTS = [0.35, 0.30, 0.20, 0.10, 0.05]

ERROR_CODES = [400, 401, 403, 404, 429, 500, 502, 503]
SUCCESS_CODES = [200, 201, 204]


def lognormal_ms(mu_ms: float = 100.0, sigma_ms: float = 50.0) -> float:
    """Sample from a log-normal distribution parameterised in linear ms."""
    mu_log = math.log(mu_ms ** 2 / math.sqrt(mu_ms ** 2 + sigma_ms ** 2))
    sigma_log = math.sqrt(math.log(1 + (sigma_ms / mu_ms) ** 2))
    return round(random.lognormvariate(mu_log, sigma_log), 3)


def random_ipv4() -> str:
    return f"{random.randint(10,192)}.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,254)}"


def make_entry(base_time: datetime, idx: int) -> dict:
    path = random.choices(PATHS, weights=PATH_WEIGHTS, k=1)[0]
    is_error = random.random() < 0.05
    status = random.choice(ERROR_CODES) if is_error else random.choice(SUCCESS_CODES)
    method = "GET" if path in ("/health", "/metrics") else random.choice(["GET", "POST", "PUT", "DELETE"])
    ts = base_time + timedelta(seconds=idx * 3)
    bytes_sent = random.randint(256, 8192)
    return {
        "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "path": path,
        "method": method,
        "status_code": status,
        "response_time_ms": lognormal_ms(),
        "bytes_sent": bytes_sent,
        "client_ip": random_ipv4(),
        "backend_name": "backend1",
    }


def send_batch(entries: list) -> dict:
    payload = json.dumps({"logs": entries}).encode()
    req = urllib.request.Request(
        f"{INGESTION_URL}/ingestion",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def main() -> None:
    random.seed(SEED)
    base_time = datetime(2026, 5, 31, 10, 0, 0, tzinfo=UTC)

    entries = [make_entry(base_time, i) for i in range(TOTAL)]

    total_accepted = 0
    total_rejected = 0
    all_errors = []

    for batch_start in range(0, TOTAL, BATCH_SIZE):
        batch = entries[batch_start : batch_start + BATCH_SIZE]
        try:
            result = send_batch(batch)
            total_accepted += result.get("accepted", 0)
            total_rejected += result.get("rejected", 0)
            all_errors.extend(result.get("errors", []))
            print(
                f"Batch {batch_start // BATCH_SIZE + 1}/{TOTAL // BATCH_SIZE}: "
                f"accepted={result['accepted']} rejected={result['rejected']}"
            )
        except Exception as exc:
            print(f"ERROR sending batch {batch_start}: {exc}", file=sys.stderr)
            total_rejected += len(batch)

    print("\n=== Seed Summary ===")
    print(f"Total entries:  {TOTAL}")
    print(f"Total accepted: {total_accepted}")
    print(f"Total rejected: {total_rejected}")
    if all_errors:
        print(f"Errors: {all_errors[:5]}")


if __name__ == "__main__":
    main()
