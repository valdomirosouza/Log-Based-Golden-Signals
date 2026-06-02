"""
Publishes GoldenSignalEvents to the Redis Stream golden-signals:events.
Falls back to an asyncio.Queue (in-memory) when Redis is unavailable.
"""

import asyncio
import json
import logging
import os

import redis.asyncio as aioredis

from .models import GoldenSignalEvent

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
STREAM_NAME = "golden-signals:events"

logger = logging.getLogger("ingestion_api.queue")

_redis: aioredis.Redis | None = None
_fallback_queue: asyncio.Queue = asyncio.Queue()


async def get_redis() -> aioredis.Redis | None:
    global _redis
    if _redis is None:
        try:
            _redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
            await _redis.ping()
        except Exception as exc:
            logger.warning('{"msg":"redis unavailable, using fallback queue","error":"%s"}', exc)
            _redis = None
    return _redis


async def publish(event: GoldenSignalEvent) -> None:
    r = await get_redis()
    payload = json.dumps(event.model_dump(mode="json"), default=str)
    if r is not None:
        try:
            await r.xadd(STREAM_NAME, {"data": payload})
            return
        except Exception as exc:
            logger.warning('{"msg":"redis publish failed, falling back","error":"%s"}', exc)
    await _fallback_queue.put(event)
