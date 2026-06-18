"""Parse/normalise unit tests for gs-log-shipper (ENV-FR-03, ADR-0084).

Verifies the pinned HAProxy line → SPEC-LGS-001 §9.1 canonical entry mapping,
epoch-millisecond timestamps, %Tr-as-latency, and robust drop-not-crash
handling of malformed/oversized untrusted input (CLAUDE.md §3.2). Issue #24.
"""

from __future__ import annotations

import pytest
from gs_log_shipper.logline import SENTINEL, CanonicalEntry, ParseError, parse_line


def _line(**over: object) -> str:
    fields = {
        "ts": "1700000000000",
        "method": "GET",
        "path": "/api/orders",
        "status": "200",
        "Tr": "12.5",
        "Tt": "30.0",
        "bytes": "1024",
        "ci": "203.0.113.42",
        "backend": "be_app",
    }
    fields.update({k: str(v) for k, v in over.items()})
    return SENTINEL + "\t" + "\t".join(f"{k}={v}" for k, v in fields.items())


def test_parses_all_required_fields() -> None:
    entry = parse_line(_line())
    assert entry == CanonicalEntry(
        timestamp=1700000000000,
        path="/api/orders",
        status_code=200,
        response_time_ms=12.5,
        bytes_sent=1024,
        client_ip="203.0.113.42",
    )


def test_wire_form_is_camelcase_matching_java_dto() -> None:
    # Grounded against services/golden-signals .../LogEntryDto + IngestionControllerTest.
    wire = parse_line(_line()).to_wire()
    assert set(wire) == {
        "path",
        "statusCode",
        "responseTimeMs",
        "bytesSent",
        "timestamp",
        "clientIp",
    }
    assert wire["statusCode"] == 200
    assert wire["responseTimeMs"] == 12.5


def test_timestamp_is_epoch_millis_int() -> None:
    entry = parse_line(_line(ts="1700000123456"))
    assert isinstance(entry.timestamp, int)
    assert entry.timestamp == 1700000123456


def test_latency_signal_is_tr_not_tt() -> None:
    # %Tr is the Golden-Signals latency field; %Tt (here 9999) is context only.
    entry = parse_line(_line(Tr="42.0", Tt="9999.0"))
    assert entry.response_time_ms == 42.0


def test_client_ip_dash_sentinel_is_absent() -> None:
    entry = parse_line(_line(ci="-"))
    assert entry.client_ip is None
    assert "clientIp" not in entry.to_wire()


@pytest.mark.parametrize(
    "over",
    [
        {"ts": "not-a-number"},
        {"status": "abc"},
        {"bytes": "-5"},
        {"Tr": "-5"},  # a genuinely-negative %Tr (not HAProxy's -1 sentinel) still raises
        {"status": "99"},
        {"status": "600"},
    ],
)
def test_malformed_numeric_fields_raise_parse_error(over: dict[str, object]) -> None:
    with pytest.raises(ParseError):
        parse_line(_line(**over))


def test_haproxy_tr_minus_one_sentinel_normalised_to_zero() -> None:
    # HAProxy emits %Tr=-1 when there is no server-side request/response (an
    # applet / `http-request return` canned backend has no upstream server).
    # That is HAProxy's documented "event did not occur" sentinel — the line is
    # valid and %Tr normalises to 0.0 ms, not dropped. Regression-locks the
    # second facet of Defect B (issue #28); grounded on a real captured frame:
    #   GSLOG ts=… status=200 Tr=-1 Tt=0 bytes=90 ci=… backend=gs_upstream
    entry = parse_line(_line(Tr="-1", Tt="0"))
    assert entry.response_time_ms == 0.0
    assert entry.to_wire()["responseTimeMs"] == 0.0


def test_missing_required_field_raises() -> None:
    line = SENTINEL + "\tpath=/x\tstatus=200\tTr=1.0\tbytes=10"  # no ts
    with pytest.raises(ParseError, match="ts"):
        parse_line(line)


def test_missing_sentinel_raises() -> None:
    with pytest.raises(ParseError, match="sentinel"):
        parse_line("GET /api/orders 200 12 1024")


def test_empty_line_raises() -> None:
    with pytest.raises(ParseError):
        parse_line("   ")


def test_oversized_line_dropped() -> None:
    huge = _line(path="/" + "a" * 9000)
    with pytest.raises(ParseError, match="max_line_bytes"):
        parse_line(huge, max_line_bytes=8192)


def test_path_passed_through_faithfully() -> None:
    # ADR-0084 §2: shipper must not corrupt/partial-decode the path.
    entry = parse_line(_line(path="/api/orders%2Fdetail?x=1"))
    assert entry.path == "/api/orders%2Fdetail?x=1"
