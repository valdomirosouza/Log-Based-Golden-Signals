"""
Metrics Processor worker.

Consumes events from the Redis Stream golden-signals:events,
aggregates into 1m/5m windows, and persists to Redis.
Exposes a /metrics HTTP endpoint via aiohttp.
"""

import asyncio
import json
import logging
import os
import signal

import redis.asyncio as aioredis
from aiohttp import web

from .aggregator import aggregate

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
STREAM_NAME = "golden-signals:events"
GROUP_NAME = "metrics-processor"
CONSUMER_NAME = "worker-0"
DLQ_KEY = "golden-signals:dlq"
METRICS_PORT = int(os.getenv("METRICS_PORT", "8002"))
MAX_RETRIES = 3

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("metrics_processor")

_stats = {"events_processed": 0, "events_dlq": 0, "lag": 0}
_shutdown: asyncio.Event = asyncio.Event()


async def _connect_redis() -> aioredis.Redis:
    delay = 1
    while True:
        try:
            r = await aioredis.from_url(REDIS_URL, decode_responses=True)
            await r.ping()
            logger.info('{"service":"metrics_processor","msg":"redis connected"}')
            return r
        except Exception as exc:
            logger.warning(
                '{"service":"metrics_processor","msg":"redis unavailable, retrying in %ds",'
                '"error":"%s"}',
                delay,
                exc,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)


async def _ensure_consumer_group(r: aioredis.Redis) -> None:
    try:
        await r.xgroup_create(STREAM_NAME, GROUP_NAME, id="0", mkstream=True)
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):  # BUSYGROUP = group already exists, normal
            logger.warning(
                '{"service":"metrics_processor","msg":"consumer group creation error",'
                '"error":"%s"}',
                exc,
            )


async def _process_loop(r: aioredis.Redis) -> None:
    retry_counts: dict[str, int] = {}

    while True:
        try:
            results = await r.xreadgroup(
                GROUP_NAME, CONSUMER_NAME, {STREAM_NAME: ">"}, count=100, block=1000
            )
        except aioredis.ResponseError as exc:
            if "NOGROUP" in str(exc):
                await _ensure_consumer_group(r)
                continue
            logger.error('{"service":"metrics_processor","msg":"redis error","error":"%s"}', exc)
            continue
        except (aioredis.ConnectionError, aioredis.TimeoutError) as exc:
            logger.error(
                '{"service":"metrics_processor","msg":"connection lost, reconnecting",'
                '"error":"%s"}',
                exc,
            )
            r = await _connect_redis()
            await _ensure_consumer_group(r)
            continue

        if not results:
            continue

        for _stream, messages in results:
            for msg_id, fields in messages:
                try:
                    event = json.loads(fields["data"])
                    await aggregate(r, event)
                    await r.xack(STREAM_NAME, GROUP_NAME, msg_id)
                    await r.xdel(STREAM_NAME, msg_id)
                    _stats["events_processed"] += 1
                    retry_counts.pop(msg_id, None)
                except Exception as exc:
                    count = retry_counts.get(msg_id, 0) + 1
                    retry_counts[msg_id] = count
                    logger.error(
                        '{"service":"metrics_processor","msg":"processing error",'
                        '"msg_id":"%s","retry":%d,"error":"%s"}',
                        msg_id,
                        count,
                        exc,
                    )
                    if count >= MAX_RETRIES:
                        await r.xadd(
                            DLQ_KEY,
                            {
                                "original_id": msg_id,
                                "data": fields.get("data", ""),
                                "error": str(exc),
                            },
                        )
                        await r.xack(STREAM_NAME, GROUP_NAME, msg_id)
                        await r.xdel(STREAM_NAME, msg_id)
                        _stats["events_dlq"] += 1
                        retry_counts.pop(msg_id, None)

        # Update lag
        try:
            info = await r.xinfo_groups(STREAM_NAME)
            for g in info:
                if g.get("name") == GROUP_NAME:
                    _stats["lag"] = g.get("lag", 0) or 0
        except Exception as exc:
            logger.debug(
                '{"service":"metrics_processor","msg":"lag update failed","error":"%s"}',
                exc,
            )

        if _shutdown.is_set():
            logger.info(
                '{"service":"metrics_processor","msg":"shutdown signal received, exiting loop"}'
            )
            return


# ── HTTP /metrics endpoint ────────────────────────────────────────────────────

async def _handle_metrics(request: web.Request) -> web.Response:
    return web.json_response(_stats)


async def _run_http_server() -> None:
    app = web.Application()
    app.router.add_get("/metrics", _handle_metrics)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", METRICS_PORT)  # noqa: S104
    await site.start()
    logger.info(
        '{"service":"metrics_processor","msg":"metrics server started","port":%d}',
        METRICS_PORT,
    )


async def main() -> None:
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown.set)
    logger.info('{"service":"metrics_processor","msg":"signal handlers registered"}')
    logger.info('{"service":"metrics_processor","msg":"starting"}')
    r = await _connect_redis()
    await _ensure_consumer_group(r)
    await asyncio.gather(_run_http_server(), _process_loop(r))


if __name__ == "__main__":
    asyncio.run(main())
