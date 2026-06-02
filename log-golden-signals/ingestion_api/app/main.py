import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware import Middleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from starlette.middleware.base import BaseHTTPMiddleware

from .auth import api_key_middleware, sha256_key
from .logging_config import configure_logging
from .models import LogBatch, LogEntry
from .queue import get_redis, publish
from .rate_limit import check_rate_limit
from .signals import extract

configure_logging()
logger = logging.getLogger("ingestion_api")

RETENTION_AUDIT_MAXLEN = int(os.getenv("RETENTION_AUDIT_MAXLEN", "100000"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Ingestion API", lifespan=lifespan)
app.add_middleware(BaseHTTPMiddleware, dispatch=api_key_middleware)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    try:
        r = await get_redis()
        await r.ping()
        return {"status": "ready"}
    except Exception:
        raise HTTPException(status_code=503, detail="Not ready")


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
                maxlen=RETENTION_AUDIT_MAXLEN,
                approximate=True,
            )
    except Exception as exc:
        logger.warning("audit write failed", extra={"trace_id": trace_id, "error": str(exc)})


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

    try:
        batch = LogBatch.model_validate(body)
    except PydanticValidationError as exc:
        await _write_audit(trace_id, "POST /ingestion", api_key, 422)
        return JSONResponse(
            status_code=422,
            content={"detail": exc.errors()},
            headers={"X-Trace-Id": trace_id},
        )
    raw_logs = batch.logs

    accepted = 0
    rejected = 0
    errors: list[dict[str, Any]] = []

    for i, entry in enumerate(raw_logs):
        try:
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
