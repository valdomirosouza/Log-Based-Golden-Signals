"""
Sliding-window rate limiter: max 100 requests/minute per API key.
Uses Redis INCR + EXPIRE for an atomic per-minute counter.
"""

import math
import time
from typing import Optional

import redis.asyncio as aioredis

RATE_LIMIT = 100
WINDOW_SECONDS = 60


def _rate_key(api_key_hash: str) -> str:
    bucket = int(time.time() / WINDOW_SECONDS)
    return f"ratelimit:{api_key_hash}:{bucket}"


async def check_rate_limit(r: aioredis.Redis, api_key_hash: str) -> tuple[bool, int]:
    """
    Returns (allowed: bool, retry_after_seconds: int).
    Increments the counter and sets a TTL on first call per window.
    """
    key = _rate_key(api_key_hash)
    try:
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, WINDOW_SECONDS)
        if count > RATE_LIMIT:
            # Seconds until the current bucket expires
            ttl = await r.ttl(key)
            return False, max(ttl, 1)
        return True, 0
    except (aioredis.ConnectionError, aioredis.TimeoutError):
        # Fail open — do not block requests if Redis is unavailable
        return True, 0
