import os
from datetime import UTC

from .models import GoldenSignalEvent, LogEntry
from .pii import mask_ip

SATURATION_BYTES_THRESHOLD = int(os.getenv("SATURATION_BYTES_THRESHOLD", "1000000"))

_WINDOW_1M = 60
_WINDOW_5M = 300


def _epoch_bucket(ts_epoch: float, window_seconds: int) -> int:
    return int(ts_epoch / window_seconds) * window_seconds


def extract(entry: LogEntry) -> GoldenSignalEvent:
    ts = entry.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    epoch = ts.timestamp()

    return GoldenSignalEvent(
        timestamp=ts,
        path=entry.path,
        method=entry.method,
        status_code=entry.status_code,
        response_time_ms=entry.response_time_ms,
        bytes_sent=entry.bytes_sent,
        client_ip_masked=mask_ip(entry.client_ip),
        backend_name=entry.backend_name,
        is_error=entry.status_code >= 400,
        window_1m=_epoch_bucket(epoch, _WINDOW_1M),
        window_5m=_epoch_bucket(epoch, _WINDOW_5M),
    )
