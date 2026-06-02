import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient

from ingestion_api.app.main import app
from ingestion_api.app.pii import mask_ip
from ingestion_api.app.signals import extract
from ingestion_api.app.models import LogEntry

client = TestClient(app)

VALID_ENTRY = {
    "timestamp": "2026-05-31T10:00:00Z",
    "path": "/api/v1/users",
    "method": "GET",
    "status_code": 200,
    "response_time_ms": 45.3,
    "bytes_sent": 1024,
    "client_ip": "192.168.1.100",
    "backend_name": "backend1",
}


# ── Valid batch ────────────────────────────────────────────────────────────────

def test_valid_batch_accepted():
    resp = client.post("/ingestion", json={"logs": [VALID_ENTRY] * 10})
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] == 10
    assert body["rejected"] == 0
    assert body["errors"] == []


# ── Schema validation ──────────────────────────────────────────────────────────

def test_missing_required_field_rejected():
    bad = {k: v for k, v in VALID_ENTRY.items() if k != "status_code"}
    resp = client.post("/ingestion", json={"logs": [bad]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["rejected"] == 1
    assert body["accepted"] == 0


def test_invalid_json_returns_422():
    resp = client.post(
        "/ingestion",
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422


# ── PII masking ────────────────────────────────────────────────────────────────

def test_ipv4_last_octet_masked():
    assert mask_ip("192.168.1.100") == "192.168.1.xxx"
    assert mask_ip("10.0.0.1") == "10.0.0.xxx"


def test_ipv6_last_80_bits_masked():
    result = mask_ip("2001:db8::1")
    addr = result
    # last 80 bits zeroed → last 5 hextets should be 0
    parts = addr.split(":")
    # expand abbreviated IPv6
    import ipaddress
    expanded = ipaddress.IPv6Address(addr).exploded
    hextets = expanded.split(":")
    assert all(h == "0000" for h in hextets[3:])


# ── Golden Signal extraction ───────────────────────────────────────────────────

def _make_entry(**kwargs) -> LogEntry:
    data = {**VALID_ENTRY, **kwargs}
    return LogEntry.model_validate(data)


def test_traffic_signal_window_keys_computed():
    entry = _make_entry(timestamp="2026-05-31T10:00:45Z")
    event = extract(entry)
    # 10:00:45 → epoch ~1748685645; 1m bucket = floor(t/60)*60
    assert event.window_1m % 60 == 0
    assert event.window_5m % 300 == 0


def test_latency_signal_preserved():
    entry = _make_entry(response_time_ms=187.5)
    event = extract(entry)
    assert event.response_time_ms == 187.5


def test_error_signal_flagged_for_4xx():
    entry = _make_entry(status_code=404)
    event = extract(entry)
    assert event.is_error is True


def test_error_signal_not_flagged_for_2xx():
    entry = _make_entry(status_code=200)
    event = extract(entry)
    assert event.is_error is False


def test_saturation_bytes_preserved():
    entry = _make_entry(bytes_sent=500_000)
    event = extract(entry)
    assert event.bytes_sent == 500_000


def test_error_signal_flagged_for_5xx():
    """5xx status codes must set is_error=True."""
    for code in [500, 502, 503, 504]:
        entry = _make_entry(status_code=code)
        event = extract(entry)
        assert event.is_error is True, f"status {code} should be an error"


def test_status_code_boundary_399_not_error_400_is_error():
    """399 is NOT an error; 400 IS an error (exact boundary)."""
    assert extract(_make_entry(status_code=399)).is_error is False
    assert extract(_make_entry(status_code=400)).is_error is True


def test_mask_ip_returns_original_on_invalid_input():
    """Malformed or non-IP strings are returned unchanged."""
    from ingestion_api.app.pii import mask_ip
    assert mask_ip("not-an-ip") == "not-an-ip"
    assert mask_ip("") == ""
    assert mask_ip("999.999.999.999") == "999.999.999.999"
