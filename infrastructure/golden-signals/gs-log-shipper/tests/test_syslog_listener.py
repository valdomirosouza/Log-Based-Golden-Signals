"""Syslog-listener unit tests for gs-log-shipper (ADR-0084 Amendment 2026-06-17).

Covers the runtime transport edge added to fix Defect B (issue #28): a syslog
frame → enveloped-stripped → recovered ``GSLOG\\t…`` line → existing
parse→batch→ship pipeline. Asserts:

* envelope stripping (``<PRI>`` + interposed header) recovers the pinned line;
* a syslog frame carrying a HAProxy access-log line yields a canonical entry
  delivered to the (fake) transport;
* a malformed / non-GSLOG / oversized frame is dropped-and-counted, never
  crashes, and never leaks a raw client_ip to stdout (ENV-NFR-04).

These exercise the pure seam (``strip_syslog_envelope`` / ``handle_frame``); the
blocking socket ``serve``/handler loop is covered by live integration, not here.
Issue #28 (Defect B), #18 (epic).
"""

from __future__ import annotations

import json

import pytest
from gs_log_shipper.config import ShipperConfig
from gs_log_shipper.logline import SENTINEL
from gs_log_shipper.shipper import IngestResult, Shipper
from gs_log_shipper.syslog_listener import handle_frame, strip_syslog_envelope

CLIENT_IP = "203.0.113.77"


def _cfg(**over: object) -> ShipperConfig:
    base = {
        "ingestion_url": "http://golden-signals:8085/ingestion",
        "api_key": "test-key-001",
        "batch_max_entries": 1,  # flush on first line so we can assert delivery
        "batch_max_seconds": 2.0,
        "max_retries": 1,
        "backoff_base_seconds": 0.0,
        "backoff_max_seconds": 0.0,
        "max_line_bytes": 8192,
        "request_timeout_seconds": 5.0,
        "syslog_host": "127.0.0.1",
        "syslog_port": 5514,
    }
    base.update(over)
    return ShipperConfig(**base)  # type: ignore[arg-type]


def _gslog(path: str = "/api/orders", ci: str = CLIENT_IP) -> str:
    return (
        f"{SENTINEL}\tts=1700000000000\tmethod=GET\tpath={path}"
        f"\tstatus=200\tTr=12.5\tTt=30.0\tbytes=1024\tci={ci}\tbackend=be"
    )


class _FakeTransport:
    """Records every delivered batch so we can assert what was shipped."""

    def __init__(self) -> None:
        self.calls: list[list[dict[str, object]]] = []

    def post_ingestion(self, url, payload, headers, timeout):  # type: ignore[no-untyped-def]
        self.calls.append(list(payload))
        return IngestResult(status_code=202, accepted=len(payload))


def _no_sleep(_seconds: float) -> None:
    return None


# -- envelope stripping -------------------------------------------------------


def test_strip_pri_prefix_recovers_gslog_line() -> None:
    framed = f"<134>{_gslog()}"
    assert strip_syslog_envelope(framed) == _gslog()


def test_strip_anchors_on_sentinel_when_header_interposed() -> None:
    # Some syslog stacks prepend a timestamp/hostname after the <PRI>.
    framed = f"<134>Jun 17 01:02:03 gs-haproxy {_gslog()}"
    assert strip_syslog_envelope(framed) == _gslog()


def test_strip_returns_non_gslog_frame_unchanged_for_downstream_drop() -> None:
    # No sentinel anywhere → returned as-is; parse_line is the final arbiter.
    assert strip_syslog_envelope("<13>not a gslog line") == "not a gslog line"


def test_strip_empty_frame_is_empty() -> None:
    assert strip_syslog_envelope("   ") == ""


# -- frame → canonical entry → delivery --------------------------------------


def test_syslog_frame_delivers_canonical_entry() -> None:
    t = _FakeTransport()
    s = Shipper(config=_cfg(), transport=t, sleep=_no_sleep)
    handle_frame(f"<134>{_gslog(path='/api/items')}\n", s)
    assert s.counters.lines_parsed_total == 1
    assert s.counters.batches_shipped_total == 1
    assert len(t.calls) == 1
    entry = t.calls[0][0]
    assert entry["path"] == "/api/items"
    assert entry["statusCode"] == 200
    assert entry["responseTimeMs"] == 12.5
    assert entry["timestamp"] == 1700000000000


def test_real_haproxy_format_raw_frame_ships() -> None:
    # The live HAProxy `log … format raw` sends NO <PRI> prefix — just the raw
    # GSLOG line — and the canned backend yields Tr=-1. This is the exact frame
    # captured on gs-net during the Defect-B fix; it MUST ship (not parse_error).
    real = "GSLOG\tts=1781658819347\tmethod=GET\tpath=/api/items\tstatus=200\tTr=-1\tTt=0\tbytes=90\tci=192.168.65.1\tbackend=gs_upstream\n"
    t = _FakeTransport()
    s = Shipper(config=_cfg(), transport=t, sleep=_no_sleep)
    handle_frame(real, s)
    assert s.counters.parse_errors_total == 0
    assert s.counters.batches_shipped_total == 1
    assert t.calls[0][0]["responseTimeMs"] == 0.0  # -1 sentinel normalised


# -- malformed / oversized frames: drop-and-count, never crash, no PII leak ---


def test_malformed_frame_dropped_and_counted(capsys: pytest.CaptureFixture[str]) -> None:
    t = _FakeTransport()
    s = Shipper(config=_cfg(), transport=t, sleep=_no_sleep)
    handle_frame("<134>GSLOG\tts=notanumber\tpath=/x\tstatus=200\tTr=1\tbytes=1\n", s)
    assert s.counters.parse_errors_total == 1
    assert t.calls == []  # nothing shipped


def test_non_gslog_frame_dropped_not_crash() -> None:
    t = _FakeTransport()
    s = Shipper(config=_cfg(), transport=t, sleep=_no_sleep)
    handle_frame("<13>random syslog noise\n", s)
    assert s.counters.parse_errors_total == 1
    assert t.calls == []


def test_oversized_frame_dropped_by_line_guard() -> None:
    t = _FakeTransport()
    s = Shipper(config=_cfg(max_line_bytes=64), transport=t, sleep=_no_sleep)
    big = _gslog(path="/api/" + "a" * 5000)
    handle_frame(f"<134>{big}\n", s)
    assert s.counters.parse_errors_total == 1
    assert t.calls == []


def test_no_raw_client_ip_leaks_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    t = _FakeTransport()
    s = Shipper(config=_cfg(), transport=t, sleep=_no_sleep)
    handle_frame(f"<134>{_gslog(ci=CLIENT_IP)}\n", s)
    out = capsys.readouterr().out
    # The IP must NOT appear in any stdout log line (ENV-NFR-04)…
    assert CLIENT_IP not in out
    # …but it MUST be present in the wire payload sent to the service (so the
    # test proves a real, non-vacuous masking guarantee).
    assert any(entry.get("clientIp") == CLIENT_IP for entry in t.calls[0])
    # Any emitted log lines are valid structured JSON, not raw frames.
    for line in out.strip().splitlines():
        if line:
            json.loads(line)
