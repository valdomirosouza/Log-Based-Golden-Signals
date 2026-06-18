"""Batch/retry/counter/PII unit tests for gs-log-shipper (ADR-0084).

Drives the at-least-once delivery, idempotent batch ids, bounded retry with
Retry-After, fail-fast 401, 422 drop, and — critically — the ENV-NFR-04
PII-no-leak guarantee that no raw client_ip is ever written to stdout.
Issue #24 (B-10).
"""

from __future__ import annotations

import io
import json
from collections.abc import Sequence

import pytest
from gs_log_shipper.config import ShipperConfig
from gs_log_shipper.logline import SENTINEL, parse_line
from gs_log_shipper.shipper import (
    FailFastAuthError,
    IngestResult,
    Shipper,
    compute_batch_id,
)

CLIENT_IP = "203.0.113.77"


def _cfg(**over: object) -> ShipperConfig:
    base = {
        "ingestion_url": "http://golden-signals:8085/ingestion",
        "api_key": "test-key-001",
        "batch_max_entries": 3,
        "batch_max_seconds": 2.0,
        "max_retries": 2,
        "backoff_base_seconds": 0.01,
        "backoff_max_seconds": 0.05,
        "max_line_bytes": 8192,
        "request_timeout_seconds": 5.0,
        "syslog_host": "127.0.0.1",
        "syslog_port": 5514,
    }
    base.update(over)
    return ShipperConfig(**base)  # type: ignore[arg-type]


def _line(path: str = "/api/orders", status: int = 200, ci: str = CLIENT_IP) -> str:
    return (
        f"{SENTINEL}\tts=1700000000000\tmethod=GET\tpath={path}"
        f"\tstatus={status}\tTr=12.5\tTt=30.0\tbytes=1024\tci={ci}\tbackend=be"
    )


class FakeTransport:
    """Scripted transport returning a queued sequence of IngestResults."""

    def __init__(self, results: list[IngestResult]) -> None:
        self._results = list(results)
        self.calls: list[tuple[Sequence[dict[str, object]], dict[str, str]]] = []

    def post_ingestion(self, url, payload, headers, timeout):
        self.calls.append((list(payload), dict(headers)))
        if self._results:
            return self._results.pop(0)
        return IngestResult(status_code=202, accepted=len(payload))


def _no_sleep(_seconds: float) -> None:
    return None


# -- batching ----------------------------------------------------------------


def test_offer_line_flushes_when_full() -> None:
    t = FakeTransport([IngestResult(202, accepted=3)])
    s = Shipper(config=_cfg(batch_max_entries=3), transport=t, sleep=_no_sleep)
    assert s.offer_line(_line()) is False
    assert s.offer_line(_line()) is False
    assert s.offer_line(_line()) is True  # batch full
    s.flush()
    assert s.counters.batches_shipped_total == 1
    assert s.counters.entries_accepted_total == 3


def test_parse_error_increments_counter_not_crash() -> None:
    t = FakeTransport([])
    s = Shipper(config=_cfg(), transport=t, sleep=_no_sleep)
    assert s.offer_line("garbage line no sentinel") is False
    assert s.counters.parse_errors_total == 1
    assert s.pending_count == 0


# -- idempotent batch id -----------------------------------------------------


def test_batch_id_is_deterministic() -> None:
    e = [parse_line(_line()), parse_line(_line(path="/api/items"))]
    assert compute_batch_id(e) == compute_batch_id(list(e))


def test_batch_id_sent_as_header_and_stable_across_retry() -> None:
    t = FakeTransport([IngestResult(503), IngestResult(202, accepted=1)])
    s = Shipper(config=_cfg(), transport=t, sleep=_no_sleep)
    s.offer_line(_line())
    s.flush(trace_id="trace-xyz")
    ids = {h["X-Batch-Id"] for _payload, h in t.calls}
    assert len(ids) == 1  # same id on retry (ADR-0084 §3)
    assert all(h["X-Trace-Id"] == "trace-xyz" for _p, h in t.calls)


# -- retry / Retry-After -----------------------------------------------------


def test_429_retries_then_succeeds() -> None:
    t = FakeTransport([IngestResult(429, retry_after_seconds=0.0), IngestResult(202, accepted=1)])
    s = Shipper(config=_cfg(), transport=t, sleep=_no_sleep)
    s.offer_line(_line())
    s.flush()
    assert s.counters.retries_total == 1
    assert s.counters.batches_shipped_total == 1
    assert s.counters.entries_dropped_total == 0


def test_retry_budget_exhausted_counts_dropped_never_silent() -> None:
    t = FakeTransport([IngestResult(503), IngestResult(503), IngestResult(503)])
    s = Shipper(config=_cfg(max_retries=2), transport=t, sleep=_no_sleep)
    s.offer_line(_line())
    s.flush()
    assert s.counters.entries_dropped_total == 1
    assert s.counters.batches_shipped_total == 0


def test_retry_after_header_honoured_and_capped() -> None:
    delays: list[float] = []
    t = FakeTransport([IngestResult(503, retry_after_seconds=999.0), IngestResult(202, accepted=1)])
    s = Shipper(config=_cfg(backoff_max_seconds=0.05), transport=t, sleep=delays.append)
    s.offer_line(_line())
    s.flush()
    assert delays == [0.05]  # capped, never the hostile 999s


# -- 401 fail-fast / 422 drop -------------------------------------------------


def test_401_fails_fast() -> None:
    t = FakeTransport([IngestResult(401)])
    s = Shipper(config=_cfg(), transport=t, sleep=_no_sleep)
    s.offer_line(_line())
    with pytest.raises(FailFastAuthError):
        s.flush()
    assert s.counters.auth_failures_total == 1


def test_422_drops_batch_with_rejected_counter() -> None:
    t = FakeTransport([IngestResult(422)])
    s = Shipper(config=_cfg(), transport=t, sleep=_no_sleep)
    s.offer_line(_line())
    s.flush()
    assert s.counters.entries_rejected_total == 1
    assert s.counters.entries_dropped_total == 0


# -- PII no-leak (ENV-NFR-04) — the load-bearing privacy test ----------------


def test_no_raw_client_ip_in_stdout_logs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Across the full happy + retry + drop paths, no raw client_ip is logged."""
    t = FakeTransport(
        [IngestResult(429, retry_after_seconds=0.0), IngestResult(503), IngestResult(503)]
    )
    s = Shipper(config=_cfg(max_retries=1), transport=t, sleep=_no_sleep)
    s.offer_line(_line(ci=CLIENT_IP))
    s.offer_line("garbage")  # parse_error log
    s.flush()
    out = capsys.readouterr().out
    assert CLIENT_IP not in out
    # The structured logs are still emitted (counts, not payload).
    events = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert any(e["event"] == "parse_error" for e in events)
    # Confirm the IP was genuinely in the wire payload (so the test is real).
    sent_payloads = [p for p, _h in t.calls]
    assert any("clientIp" in entry for entry in sent_payloads[0])


def test_structured_log_is_json(capsys: pytest.CaptureFixture[str]) -> None:
    t = FakeTransport([IngestResult(202, accepted=1)])
    s = Shipper(config=_cfg(), transport=t, sleep=_no_sleep)
    s.offer_line(_line())
    s.flush(trace_id="t1")
    line = capsys.readouterr().out.strip().splitlines()[-1]
    record = json.loads(line)
    assert record["component"] == "gs-log-shipper"
    assert record["event"] == "batch_shipped"
    assert record["trace_id"] == "t1"


# -- config fail-fast on missing key -----------------------------------------


def test_config_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GS_API_KEYS", raising=False)
    with pytest.raises(ValueError, match="GS_API_KEYS is required"):
        ShipperConfig.from_env()


def test_config_takes_first_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GS_API_KEYS", "k1,k2,k3")
    assert ShipperConfig.from_env().api_key == "k1"


def test_empty_flush_is_noop() -> None:
    t = FakeTransport([])
    s = Shipper(config=_cfg(), transport=t, sleep=_no_sleep)
    s.flush()
    assert t.calls == []


def test_run_loop_final_flush_ships_remainder() -> None:
    from gs_log_shipper.main import run

    t = FakeTransport([IngestResult(202, accepted=2)])
    cfg = _cfg(batch_max_entries=100, batch_max_seconds=999.0)
    s = Shipper(config=cfg, transport=t, sleep=_no_sleep)
    lines = io.StringIO(_line() + "\n" + _line(path="/api/items") + "\n")
    run(cfg, s, iter(lines), now=lambda: 0.0)
    assert s.counters.batches_shipped_total == 1
    assert s.counters.entries_accepted_total == 2
