import hashlib
import os

from fastapi import Request
from fastapi.responses import JSONResponse

_PUBLIC_PATHS = {"/health"}


def sha256_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def api_key_middleware(request: Request, call_next):
    if request.url.path in _PUBLIC_PATHS:
        return await call_next(request)
    # Check env var at request time so tests can control it without module reload
    required_key = os.getenv("INGESTION_API_KEY", "")
    if not required_key:
        # Auth disabled — no key configured
        return await call_next(request)
    provided = request.headers.get("X-API-Key", "")
    if provided != required_key:
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)
