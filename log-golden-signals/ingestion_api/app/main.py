import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware import Middleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .auth import api_key_middleware, sha256_key
from .logging_config import configure_logging
from .queue import get_redis, publish
from .rate_limit import check_rate_limit
from .signals import extract

configure_logging()
logger = logging.getLogger("ingestion_api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Ingestion API", lifespan=lifespan)
app.add_middleware(BaseHTTPMiddleware, dispatch=api_key_middleware)


@app.get("/health")
async def health():
    return {"status": "ok"}


async def _write_audit(trace_id: str, endpoint: str, api_key: str, status_code: int) -> None:
    try:
        r = await get_redis()
        if r:
            await r.xadd(
                "golden-signals:audit",
                {
                    "trace_id": trace_id,
                    "endpoint": endpoint,
                    "api_key_hash": sha256_key(api_key),
                    "status_code": str(status_code),
                },
            )
    except Exception:
        pass


@app.post("/ingestion")
async def ingest(request: Request) -> JSONResponse:
    trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))
    api_key = request.headers.get("X-API-Key", "")

    # Rate limiting
    try:
        r = await get_redis()
    except Exception:
        r = None

    if r and api_key:
        import os
        if os.getenv("INGESTION_API_KEY", ""):  # only rate-limit when auth is enabled
            allowed, retry_after = await check_rate_limit(r, sha256_key(api_key))
            if not allowed:
                await _write_audit(trace_id, "POST /ingestion", api_key, 429)
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded"},
                    headers={
                        "X-Trace-Id": trace_id,
                        "Retry-After": str(retry_after),
                    },
                )

    try:
        body = await request.json()
    except Exception:
        await _write_audit(trace_id, "POST /ingestion", api_key, 422)
        return JSONResponse(
            status_code=422,
            content={"detail": "Invalid JSON body"},
            headers={"X-Trace-Id": trace_id},
        )

    accepted = 0
    rejected = 0
    errors: list[dict[str, Any]] = []

    raw_logs = body.get("logs", []) if isinstance(body, dict) else []

    for i, raw in enumerate(raw_logs):
        try:
            from .models import LogEntry
            entry = LogEntry.model_validate(raw)
            event = extract(entry)
            await publish(event)
            accepted += 1
        except Exception as exc:
            rejected += 1
            errors.append({"index": i, "detail": str(exc)})

    logger.info(
        "ingestion complete",
        extra={"trace_id": trace_id, "accepted": accepted, "rejected": rejected},
    )

    await _write_audit(trace_id, "POST /ingestion", api_key, 200)

    return JSONResponse(
        status_code=200,
        content={"accepted": accepted, "rejected": rejected, "errors": errors},
        headers={"X-Trace-Id": trace_id},
    )
