"""Transport-layer unit tests for gs-log-shipper (the httpx boundary).

Drives HttpxTransport with a fake httpx.Client so the response→IngestResult
mapping, Retry-After parsing, and error→503 fallback are covered without a live
network. Issue #24 (B-10).
"""

from __future__ import annotations

import httpx
import pytest
from gs_log_shipper.transport import HttpxTransport, _parse_retry_after


class FakeResponse:
    def __init__(self, status_code: int, body=None, headers=None) -> None:
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeClient:
    def __init__(self, response=None, raise_exc=None) -> None:
        self._response = response
        self._raise = raise_exc
        self.calls = 0

    def post(self, url, json, headers, timeout):
        self.calls += 1
        if self._raise is not None:
            raise self._raise
        return self._response


def _payload():
    return [
        {"path": "/x", "statusCode": 200, "responseTimeMs": 1.0, "bytesSent": 1, "timestamp": 1}
    ]


def test_202_maps_accepted_rejected() -> None:
    client = FakeClient(FakeResponse(202, body={"accepted": 5, "rejected": 1}))
    t = HttpxTransport(client=client)  # type: ignore[arg-type]
    result = t.post_ingestion("http://svc/ingestion", _payload(), {}, 5.0)
    assert result.status_code == 202
    assert result.accepted == 5
    assert result.rejected == 1


def test_202_unparsable_body_counts_payload_len() -> None:
    client = FakeClient(FakeResponse(202, body=ValueError("bad json")))
    t = HttpxTransport(client=client)  # type: ignore[arg-type]
    result = t.post_ingestion("http://svc/ingestion", _payload(), {}, 5.0)
    assert result.status_code == 202
    assert result.accepted == 1


def test_429_with_retry_after_header() -> None:
    client = FakeClient(FakeResponse(429, headers={"Retry-After": "7"}))
    t = HttpxTransport(client=client)  # type: ignore[arg-type]
    result = t.post_ingestion("http://svc/ingestion", _payload(), {}, 5.0)
    assert result.status_code == 429
    assert result.retry_after_seconds == 7.0


def test_timeout_maps_to_503() -> None:
    client = FakeClient(raise_exc=httpx.TimeoutException("slow"))
    t = HttpxTransport(client=client)  # type: ignore[arg-type]
    assert t.post_ingestion("http://svc/ingestion", _payload(), {}, 5.0).status_code == 503


def test_generic_http_error_maps_to_503() -> None:
    client = FakeClient(raise_exc=httpx.ConnectError("refused"))
    t = HttpxTransport(client=client)  # type: ignore[arg-type]
    assert t.post_ingestion("http://svc/ingestion", _payload(), {}, 5.0).status_code == 503


@pytest.mark.parametrize(
    "value,expected",
    [(None, None), ("", None), ("abc", None), ("-3", None), ("5", 5.0), ("2.5", 2.5)],
)
def test_parse_retry_after(value, expected) -> None:
    assert _parse_retry_after(value) == expected
