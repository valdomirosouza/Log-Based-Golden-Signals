import asyncio
import logging
import os

import redis.asyncio as aioredis

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("metrics_processor")


async def connect_redis() -> aioredis.Redis:
    return await aioredis.from_url(REDIS_URL, decode_responses=True)


async def main() -> None:
    logger.info('{"service": "metrics_processor", "message": "starting"}')
    redis = await connect_redis()
    await redis.ping()
    logger.info('{"service": "metrics_processor", "message": "redis connected"}')
    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
