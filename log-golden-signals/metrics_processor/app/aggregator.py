"""
Aggregation logic: updates Redis keys for each Golden Signal and window.

Key patterns (per spec):
  gs:{signal}:{path}:{window}:{epoch_bucket}

  signal = latency | traffic | error | error_total | saturation
  window  = 1m | 5m
"""

import logging
import os
from typing import Any

import redis.asyncio as aioredis

RETENTION_1M = int(os.getenv("RETENTION_1M_SECONDS", "7200"))
RETENTION_5M = int(os.getenv("RETENTION_5M_SECONDS", "86400"))

logger = logging.getLogger("metrics_processor.aggregator")


def _key(signal: str, path: str, window: str, bucket: int) -> str:
    safe_path = path.replace(":", "_")
    return f"gs:{signal}:{safe_path}:{window}:{bucket}"


async def _set_ttl(r: aioredis.Redis, key: str, window: str) -> None:
    ttl = RETENTION_1M if window == "1m" else RETENTION_5M
    await r.expire(key, ttl)


async def aggregate(r: aioredis.Redis, event: dict[str, Any]) -> None:
    path = event["path"]
    response_time_ms = float(event["response_time_ms"])
    bytes_sent = int(event["bytes_sent"])
    is_error = bool(event["is_error"])

    for window_label, bucket in (("1m", event["window_1m"]), ("5m", event["window_5m"])):
        # Traffic — INCR counter
        t_key = _key("traffic", path, window_label, bucket)
        await r.incr(t_key)
        await _set_ttl(r, t_key, window_label)

        # Latency — ZADD sorted set (score = value, member = value:uuid for uniqueness)
        # Score == response_time_ms so ZRANGEBYSCORE gives rank-ordered values.
        l_key = _key("latency", path, window_label, bucket)
        import uuid
        await r.zadd(l_key, {f"{response_time_ms}:{uuid.uuid4().hex}": response_time_ms})
        await _set_ttl(r, l_key, window_label)

        # Error count and total request count
        if is_error:
            e_key = _key("error", path, window_label, bucket)
            await r.incr(e_key)
            await _set_ttl(r, e_key, window_label)

        et_key = _key("error_total", path, window_label, bucket)
        await r.incr(et_key)
        await _set_ttl(r, et_key, window_label)

        # Saturation — accumulate bytes_sent
        s_key = _key("saturation", path, window_label, bucket)
        await r.incrbyfloat(s_key, bytes_sent)
        await _set_ttl(r, s_key, window_label)

    # Track seen paths in a Redis set for /analytics/paths
    await r.sadd("gs:paths", path)
