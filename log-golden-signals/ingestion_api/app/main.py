import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .queue import publish
from .signals import extract

logger = logging.getLogger("ingestion_api")
logging.basicConfig(level=logging.INFO, format="%(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Ingestion API", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/ingestion")
async def ingest(request: Request) -> JSONResponse:
    trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=422,
            content={"detail": "Invalid JSON body"},
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
        except (ValidationError, Exception) as exc:
            rejected += 1
            errors.append({"index": i, "detail": str(exc)})

    logger.info(
        '{"service":"ingestion_api","trace_id":"%s","accepted":%d,"rejected":%d}',
        trace_id, accepted, rejected,
    )

    return JSONResponse(
        status_code=200,
        content={"accepted": accepted, "rejected": rejected, "errors": errors},
        headers={"X-Trace-Id": trace_id},
    )
