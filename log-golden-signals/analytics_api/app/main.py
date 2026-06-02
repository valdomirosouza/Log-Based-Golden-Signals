import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .auth import api_key_middleware, sha256_key
from .logging_config import configure_logging
from .query import (
    _buckets_for_range,
    query_error,
    query_latency,
    query_saturation,
    query_traffic,
    summarise_latency,
)
from .redis_client import get_redis, is_connected

configure_logging()
logger = logging.getLogger("analytics_api")

RETENTION_AUDIT_MAXLEN = int(os.getenv("RETENTION_AUDIT_MAXLEN", "100000"))
MAX_RANGE_SECONDS = int(os.getenv("ANALYTICS_MAX_RANGE_SECONDS", str(7 * 24 * 3600)))

_HITL_P99_THRESHOLD_MS = 500.0
_HITL_ERROR_RATE_THRESHOLD = 0.05


class Signal(str, Enum):
    latency = "latency"
    traffic = "traffic"
    error = "error"
    saturation = "saturation"


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Analytics API", lifespan=lifespan)
app.add_middleware(BaseHTTPMiddleware, dispatch=api_key_middleware)


def _governance(summary: Optional[dict]) -> dict:
    hitl = False
    if summary:
        p99 = summary.get("p99_ms", 0) or 0
        err = summary.get("avg_error_rate", 0) or 0
        hitl = p99 >= _HITL_P99_THRESHOLD_MS or err >= _HITL_ERROR_RATE_THRESHOLD
    return {
        "data_classification": "operational-telemetry",
        "pii_sanitized": True,
        "retention_policy": "1m:2h / 5m:24h",
        "audit_trail": "redis-stream:golden-signals:events",
        "recommended_action_mode": "HITL" if hitl else "HOTL",
        "human_approval_required": hitl,
    }


async def _write_audit(
    r, trace_id: str, endpoint: str, api_key: str, status_code: int
) -> None:
    try:
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


@app.get("/analytics/health")
async def analytics_health():
    connected = await is_connected()
    tracked = 0
    if connected:
        try:
            r = await get_redis()
            tracked = await r.scard("gs:paths")
        except Exception as exc:
            logger.warning("analytics_health scard failed", extra={"error": str(exc)})
    return {"status": "ok", "redis_connected": connected, "tracked_paths": tracked}


@app.get("/analytics/paths")
async def analytics_paths():
    try:
        r = await get_redis()
        paths = await r.smembers("gs:paths")
        return {"paths": sorted(paths)}
    except Exception as exc:
        logger.error("Redis unavailable", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")


@app.get("/audit")
async def audit_log(limit: int = Query(50, ge=1, le=500)):
    try:
        r = await get_redis()
        # Read last `limit` entries from the audit stream
        entries = await r.xrevrange("golden-signals:audit", count=limit)
        result = []
        for msg_id, fields in entries:
            result.append({"id": msg_id, **fields})
        return {"audit": result, "count": len(result)}
    except Exception as exc:
        logger.error("Redis unavailable", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")


@app.get("/analytics")
async def analytics(
    request: Request,
    path: str = Query(..., description="Request path to query"),
    signal: Signal = Query(..., description="Golden signal"),
    window: str = Query(..., pattern="^(1m|5m)$", description="1m or 5m"),
    from_ts: Optional[datetime] = Query(None, alias="from"),
    to_ts: Optional[datetime] = Query(None, alias="to"),
) -> JSONResponse:
    trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))
    api_key = request.headers.get("X-API-Key", "")

    now = datetime.now(timezone.utc)
    if from_ts is None:
        from_ts = datetime.fromtimestamp(now.timestamp() - 3600, tz=timezone.utc)
    if to_ts is None:
        to_ts = now
    if from_ts.tzinfo is None:
        from_ts = from_ts.replace(tzinfo=timezone.utc)
    if to_ts.tzinfo is None:
        to_ts = to_ts.replace(tzinfo=timezone.utc)

    if (to_ts - from_ts).total_seconds() > MAX_RANGE_SECONDS:
        raise HTTPException(status_code=400, detail="Time range exceeds maximum allowed (7 days)")

    try:
        r = await get_redis()
    except Exception as exc:
        logger.error("Redis unavailable", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    buckets_list = _buckets_for_range(from_ts.timestamp(), to_ts.timestamp(), window)

    if signal == Signal.latency:
        data = await query_latency(r, path, window, buckets_list)
        summary = summarise_latency(data)
    elif signal == Signal.traffic:
        data = await query_traffic(r, path, window, buckets_list)
        summary = {"total_requests": sum(b["count"] for b in data)} if data else None
    elif signal == Signal.error:
        data = await query_error(r, path, window, buckets_list)
        if data:
            avg_err = sum(b["error_rate"] for b in data) / len(data)
            summary = {"avg_error_rate": round(avg_err, 6), "total_requests": sum(b["count"] for b in data)}
        else:
            summary = None
    else:  # saturation
        data = await query_saturation(r, path, window, buckets_list)
        if data:
            avg_sat = sum(b["saturation_pct"] for b in data) / len(data)
            summary = {"avg_saturation_pct": round(avg_sat, 6)}
        else:
            summary = None

    await _write_audit(r, trace_id, f"GET /analytics signal={signal.value}", api_key, 200)

    return JSONResponse(
        content={
            "path": path,
            "signal": signal.value,
            "window": window,
            "from": from_ts.isoformat(),
            "to": to_ts.isoformat(),
            "buckets": data,
            "summary": summary,
            "_governance": _governance(summary),
        },
        headers={"X-Trace-Id": trace_id},
    )
