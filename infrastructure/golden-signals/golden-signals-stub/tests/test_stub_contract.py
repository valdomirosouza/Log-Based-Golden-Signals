"""Contract tests for the golden-signals stub (SPEC-LGS-001 §8 shape only).

Spins the stub on an ephemeral port and asserts the 202/200/401/422/429/503
paths the compose env + the shipper depend on. No application-logic assertions
(the stub owns none — CLAUDE.md §3.4). Issue #26.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer

import pytest
import stub as stub_mod

KEY = "test-key-001"


@pytest.fixture()
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("GS_API_KEYS", KEY)
    for var in ("GS_STUB_INGESTION_STATUS", "GS_STUB_HEALTH_STATUS"):
        monkeypatch.delenv(var, raising=False)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), stub_mod.StubHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()


def _post(base: str, body, key: str | None = KEY):
    data = json.dumps(body).encode() if not isinstance(body, bytes) else body
    req = urllib.request.Request(base + "/ingestion", data=data, method="POST")
    if key is not None:
        req.add_header("X-API-Key", key)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, None


def _get(base: str, path: str, key: str | None = None):
    req = urllib.request.Request(base + path, method="GET")
    if key is not None:
        req.add_header("X-API-Key", key)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, None


_VALID = [{"path": "/x", "statusCode": 200, "responseTimeMs": 1.0, "bytesSent": 1, "timestamp": 1}]


def test_ingestion_202_with_counts(server: str) -> None:
    status, body = _post(server, _VALID)
    assert status == 202
    assert body == {"accepted": 1, "rejected": 0}


def test_ingestion_counts_rejected_missing_fields(server: str) -> None:
    status, body = _post(server, [*_VALID, {"path": "/y"}])
    assert status == 202
    assert body == {"accepted": 1, "rejected": 1}


def test_ingestion_401_without_key(server: str) -> None:
    assert _post(server, _VALID, key=None)[0] == 401


def test_ingestion_422_non_array(server: str) -> None:
    assert _post(server, {"not": "an array"})[0] == 422


def test_ingestion_422_malformed_json(server: str) -> None:
    assert _post(server, b"{ not json")[0] == 422


def test_health_200_no_auth(server: str) -> None:
    status, body = _get(server, "/analytics/health")
    assert status == 200
    assert body["status"] == "ok"
    assert set(body) >= {"status", "store_connected", "tracked_paths"}


def test_analytics_401_without_key(server: str) -> None:
    assert _get(server, "/analytics?path=/x")[0] == 401


def test_analytics_paths_200_with_key(server: str) -> None:
    assert _get(server, "/analytics/paths", key=KEY)[0] == 200


def test_forced_ingestion_429(monkeypatch: pytest.MonkeyPatch, server: str) -> None:
    monkeypatch.setenv("GS_STUB_INGESTION_STATUS", "429")
    assert _post(server, _VALID)[0] == 429


def test_forced_health_503(monkeypatch: pytest.MonkeyPatch, server: str) -> None:
    monkeypatch.setenv("GS_STUB_HEALTH_STATUS", "503")
    assert _get(server, "/analytics/health")[0] == 503
