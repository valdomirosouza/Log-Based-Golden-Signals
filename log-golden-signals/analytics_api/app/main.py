import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from .query import (
    _buckets_for_range,
    query_error,
    query_latency,
    query_saturation,
    query_traffic,
    summarise_latency,
)
from .redis_client import get_redis, is_connected

logger = logging.getLogger("analytics_api")
logging.basicConfig(level=logging.INFO, format="%(message)s")


class Signal(str, Enum):
    latency = "latency"
    traffic = "traffic"
    error = "error"
    saturation = "saturation"


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Analytics API", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/analytics/health")
async def analytics_health():
    connected = await is_connected()
    tracked = 0
    if connected:
        try:
            r = await get_redis()
            tracked = await r.scard("gs:paths")
        except Exception:
            pass
    return {"status": "ok", "redis_connected": connected, "tracked_paths": tracked}


@app.get("/analytics/paths")
async def analytics_paths():
    try:
        r = await get_redis()
        paths = await r.smembers("gs:paths")
        return {"paths": sorted(paths)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {exc}")


@app.get("/analytics")
async def analytics(
    path: str = Query(..., description="Request path to query"),
    signal: Signal = Query(..., description="Golden signal"),
    window: str = Query(..., pattern="^(1m|5m)$", description="1m or 5m"),
    from_ts: Optional[datetime] = Query(None, alias="from"),
    to_ts: Optional[datetime] = Query(None, alias="to"),
) -> JSONResponse:
    now = datetime.now(timezone.utc)

    if from_ts is None:
        from_ts = datetime.fromtimestamp(now.timestamp() - 3600, tz=timezone.utc)
    if to_ts is None:
        to_ts = now

    if from_ts.tzinfo is None:
        from_ts = from_ts.replace(tzinfo=timezone.utc)
    if to_ts.tzinfo is None:
        to_ts = to_ts.replace(tzinfo=timezone.utc)

    try:
        r = await get_redis()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {exc}")

    buckets = _buckets_for_range(from_ts.timestamp(), to_ts.timestamp(), window)

    if signal == Signal.latency:
        data = await query_latency(r, path, window, buckets)
        summary = summarise_latency(data)
    elif signal == Signal.traffic:
        data = await query_traffic(r, path, window, buckets)
        summary = {"total_requests": sum(b["count"] for b in data)} if data else None
    elif signal == Signal.error:
        data = await query_error(r, path, window, buckets)
        if data:
            avg_err = sum(b["error_rate"] for b in data) / len(data)
            summary = {"avg_error_rate": round(avg_err, 6), "total_requests": sum(b["count"] for b in data)}
        else:
            summary = None
    else:  # saturation
        data = await query_saturation(r, path, window, buckets)
        if data:
            avg_sat = sum(b["saturation_pct"] for b in data) / len(data)
            summary = {"avg_saturation_pct": round(avg_sat, 6)}
        else:
            summary = None

    return JSONResponse(content={
        "path": path,
        "signal": signal.value,
        "window": window,
        "from": from_ts.isoformat(),
        "to": to_ts.isoformat(),
        "buckets": data,
        "summary": summary,
    })
