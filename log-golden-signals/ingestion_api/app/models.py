from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class LogEntry(BaseModel):
    timestamp: datetime
    path: str
    method: str
    status_code: int
    response_time_ms: float = Field(ge=0.0)
    bytes_sent: int = Field(ge=0)
    client_ip: str
    backend_name: str | None = None


class LogBatch(BaseModel):
    logs: list[LogEntry]

    @field_validator("logs")
    @classmethod
    def validate_batch_size(cls, v: list) -> list:
        if not 1 <= len(v) <= 1000:
            raise ValueError("batch must contain 1-1000 entries")
        return v


class GoldenSignalEvent(BaseModel):
    timestamp: datetime
    path: str
    method: str
    status_code: int
    response_time_ms: float
    bytes_sent: int
    client_ip_masked: str
    backend_name: str | None = None
    is_error: bool
    window_1m: int
    window_5m: int
