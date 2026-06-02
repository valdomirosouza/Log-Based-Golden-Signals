"""
Reads aggregated metrics from Redis and computes analytics buckets.
"""

import logging
import os
import re
from typing import Any, Optional

import redis.asyncio as aioredis

from .percentiles import percentile

logger = logging.getLogger("analytics_api.query")

_VALID_PATH_RE = re.compile(r'^[a-zA-Z0-9_.~/:@-]+$')


def validate_path(path: str) -> None:
    """Raise ValueError if path contains characters unsafe for Redis keys."""
    if not _VALID_PATH_RE.match(path):
        raise ValueError(f"Invalid path: contains disallowed characters")

SATURATION_BYTES_THRESHOLD = float(os.getenv("SATURATION_BYTES_THRESHOLD", "1_000_000"))
_WINDOW_SECONDS = {"1m": 60, "5m": 300}


def _key(signal: str, path: str, window: str, bucket: int) -> str:
    safe_path = path.replace(":", "_")
    return f"gs:{signal}:{safe_path}:{window}:{bucket}"


def _buckets_for_range(from_epoch: float, to_epoch: float, window: str) -> list[int]:
    step = _WINDOW_SECONDS[window]
    start = int(from_epoch / step) * step
    buckets = []
    t = start
    while t <= to_epoch:
        buckets.append(t)
        t += step
    return buckets


async def query_latency(
    r: aioredis.Redis, path: str, window: str, buckets: list[int]
) -> list[dict[str, Any]]:
    validate_path(path)
    results = []
    for bucket in buckets:
        l_key = _key("latency", path, window, bucket)
        t_key = _key("traffic", path, window, bucket)
        e_key = _key("error", path, window, bucket)
        et_key = _key("error_total", path, window, bucket)
        s_key = _key("saturation", path, window, bucket)

        try:
            raw_values = await r.zrange(l_key, 0, -1, withscores=True)
        except (aioredis.ConnectionError, aioredis.TimeoutError) as exc:
            logger.warning("Redis read error", extra={"bucket": bucket, "error": str(exc)})
            continue

        if not raw_values:
            continue

        scores = [score for _, score in raw_values]
        scores.sort()

        traffic_raw = await r.get(t_key)
        error_raw = await r.get(e_key)
        error_total_raw = await r.get(et_key)
        sat_raw = await r.get(s_key)

        count = int(traffic_raw) if traffic_raw else len(scores)
        errors = int(error_raw) if error_raw else 0
        total_req = int(error_total_raw) if error_total_raw else count
        sat_bytes = float(sat_raw) if sat_raw else 0.0

        error_rate = errors / total_req if total_req > 0 else 0.0
        sat_pct = sat_bytes / SATURATION_BYTES_THRESHOLD

        results.append({
            "epoch_bucket": bucket,
            "p50_ms": round(percentile(scores, 50) or 0.0, 3),
            "p95_ms": round(percentile(scores, 95) or 0.0, 3),
            "p99_ms": round(percentile(scores, 99) or 0.0, 3),
            "count": count,
            "error_rate": round(error_rate, 6),
            "saturation_pct": round(sat_pct, 6),
        })
    return results


async def query_traffic(
    r: aioredis.Redis, path: str, window: str, buckets: list[int]
) -> list[dict[str, Any]]:
    validate_path(path)
    results = []
    for bucket in buckets:
        t_key = _key("traffic", path, window, bucket)
        try:
            val = await r.get(t_key)
        except (aioredis.ConnectionError, aioredis.TimeoutError) as exc:
            logger.warning("Redis read error", extra={"bucket": bucket, "error": str(exc)})
            continue
        if val is None:
            continue
        results.append({"epoch_bucket": bucket, "count": int(val)})
    return results


async def query_error(
    r: aioredis.Redis, path: str, window: str, buckets: list[int]
) -> list[dict[str, Any]]:
    validate_path(path)
    results = []
    for bucket in buckets:
        e_key = _key("error", path, window, bucket)
        et_key = _key("error_total", path, window, bucket)
        try:
            errors = await r.get(e_key)
            total = await r.get(et_key)
        except (aioredis.ConnectionError, aioredis.TimeoutError) as exc:
            logger.warning("Redis read error", extra={"bucket": bucket, "error": str(exc)})
            continue
        if total is None:
            continue
        e = int(errors) if errors else 0
        t = int(total) if total else 1
        results.append({
            "epoch_bucket": bucket,
            "count": t,
            "error_rate": round(e / t, 6),
        })
    return results


async def query_saturation(
    r: aioredis.Redis, path: str, window: str, buckets: list[int]
) -> list[dict[str, Any]]:
    validate_path(path)
    results = []
    for bucket in buckets:
        s_key = _key("saturation", path, window, bucket)
        try:
            val = await r.get(s_key)
        except (aioredis.ConnectionError, aioredis.TimeoutError) as exc:
            logger.warning("Redis read error", extra={"bucket": bucket, "error": str(exc)})
            continue
        if val is None:
            continue
        sat_pct = float(val) / SATURATION_BYTES_THRESHOLD
        results.append({
            "epoch_bucket": bucket,
            "saturation_pct": round(sat_pct, 6),
        })
    return results


def summarise_latency(buckets: list[dict]) -> Optional[dict]:
    if not buckets:
        return None
    all_p50 = [b["p50_ms"] for b in buckets]
    all_p95 = [b["p95_ms"] for b in buckets]
    all_p99 = [b["p99_ms"] for b in buckets]
    total_req = sum(b["count"] for b in buckets)
    avg_err = sum(b["error_rate"] for b in buckets) / len(buckets)
    avg_sat = sum(b["saturation_pct"] for b in buckets) / len(buckets)
    return {
        "p50_ms": round(percentile(sorted(all_p50), 50) or 0.0, 3),
        "p95_ms": round(percentile(sorted(all_p95), 95) or 0.0, 3),
        "p99_ms": round(percentile(sorted(all_p99), 99) or 0.0, 3),
        "total_requests": total_req,
        "avg_error_rate": round(avg_err, 6),
        "avg_saturation_pct": round(avg_sat, 6),
    }
