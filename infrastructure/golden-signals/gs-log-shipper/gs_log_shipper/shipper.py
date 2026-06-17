"""Batch, idempotent-id, and at-least-once delivery for gs-log-shipper.

Implements ADR-0084's delivery posture (ENV-FR-03/04):

* **Batching** — accumulate parsed entries up to ``batch_max_entries`` then
  flush; a time-based flush is the caller's responsibility (the worker loop).
* **Idempotent batch ids** — each batch carries a deterministic id derived from
  its contents, so a retry of the *same* batch is recognisable downstream and
  bounds double-counting (ADR-0084 §3).
* **At-least-once** — on ambiguous ``5xx``/``429`` the *same* batch (same id)
  is retried with bounded backoff honouring ``Retry-After``; after the budget
  is exhausted the undelivered entries are **counted** as dropped, never
  silently discarded (ADR-0084 §4).
* **Fail-fast on 401** — a bad key is a config error, surfaced with a clear
  diagnostic, not masked as success (ADR-0084 §4, AC-05).
* **422** — a bad batch is logged and dropped with a validation counter.

PII (ENV-NFR-04): the shipper transmits ``client_ip`` only over the internal
network and **never** writes a raw client IP to its own stdout. The structured
log lines below carry counts, status, batch id, and trace id — never payload.

Refs: SPEC-LGS-002 §8/§9.1, ADR-0084, ADR-0003. Issue #24 (B-10).
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from .config import ShipperConfig
from .logline import CanonicalEntry, ParseError, parse_line


@dataclass
class ShipperCounters:
    """Observable counters (ENV-FR-04: never silently discard).

    These mirror the ``gs_shipper_*`` metric family named in the spec/ADR;
    exposing them as plain counters keeps the demonstration rig dependency-free
    while remaining inspectable in tests and logs.
    """

    parse_errors_total: int = 0
    lines_parsed_total: int = 0
    batches_shipped_total: int = 0
    entries_accepted_total: int = 0
    entries_rejected_total: int = 0
    entries_dropped_total: int = 0
    retries_total: int = 0
    auth_failures_total: int = 0


@dataclass(frozen=True)
class IngestResult:
    """Outcome of a single HTTP delivery attempt."""

    status_code: int
    accepted: int = 0
    rejected: int = 0
    retry_after_seconds: float | None = None


class Transport(Protocol):
    """HTTP transport seam — keeps delivery testable without a live network."""

    def post_ingestion(
        self,
        url: str,
        payload: Sequence[dict[str, object]],
        headers: dict[str, str],
        timeout: float,
    ) -> IngestResult:
        """POST a JSON batch to ``/ingestion`` and return the parsed result."""
        ...


def _log(event: str, **fields: object) -> None:
    """Emit one structured JSON log line to stdout (ENV-NFR-03).

    NEVER pass a raw ``client_ip`` here (ENV-NFR-04); callers pass only counts,
    status, batch id, and trace id.
    """
    record = {"ts": int(time.time() * 1000), "component": "gs-log-shipper", "event": event}
    record.update(fields)
    sys.stdout.write(json.dumps(record, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def compute_batch_id(entries: Sequence[CanonicalEntry]) -> str:
    """Derive a deterministic idempotent batch id from batch contents.

    The id is a SHA-256 over the canonical wire form of every entry. Identical
    content (a retry of the same batch) yields the same id, so a service-side
    dedup can recognise it and bound over-count (ADR-0084 §3). Content includes
    ``client_ip`` for uniqueness but the id itself is a hash — it never leaks
    the raw IP.
    """
    hasher = hashlib.sha256()
    for entry in entries:
        hasher.update(json.dumps(entry.to_wire(), sort_keys=True).encode("utf-8"))
        hasher.update(b"\x1e")  # record separator
    return hasher.hexdigest()


@dataclass
class FailFastAuthError(RuntimeError):
    """Raised on a 401 — a bad API key is a config error, not a transient one."""

    message: str = "ingestion returned 401 — invalid GS_API_KEYS (fail-fast, ADR-0084 §4)"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


@dataclass
class Shipper:
    """Stateful batcher + at-least-once deliverer.

    ``transport`` is injected so unit tests drive the retry/counter/PII logic
    deterministically without a network. ``sleep`` is injectable so backoff is
    instant under test.
    """

    config: ShipperConfig
    transport: Transport
    sleep: Callable[[float], None] = time.sleep
    counters: ShipperCounters = field(default_factory=ShipperCounters)
    _pending: list[CanonicalEntry] = field(default_factory=list)

    # -- ingestion of HAProxy lines ------------------------------------------

    def offer_line(self, line: str) -> bool:
        """Parse one HAProxy line and buffer it; return True when a flush is due.

        A malformed/oversized line is dropped with ``parse_errors_total++`` and
        never crashes the shipper (ADR-0084 §4). The raw line is **not** logged
        (it may carry a client IP — ENV-NFR-04); only the error class is.
        """
        try:
            entry = parse_line(line, max_line_bytes=self.config.max_line_bytes)
        except ParseError as exc:
            self.counters.parse_errors_total += 1
            _log("parse_error", reason=str(exc))
            return False
        self.counters.lines_parsed_total += 1
        self._pending.append(entry)
        return len(self._pending) >= self.config.batch_max_entries

    @property
    def pending_count(self) -> int:
        """Number of buffered, not-yet-shipped entries."""
        return len(self._pending)

    # -- delivery ------------------------------------------------------------

    def flush(self, *, trace_id: str | None = None) -> None:
        """Ship the buffered batch at-least-once, then clear the buffer.

        Honours ``Retry-After`` on 429/503 within the bounded retry budget; a
        401 fails fast; a 422 drops the batch with a rejected counter; budget
        exhaustion counts the remainder as dropped (never silent).
        """
        if not self._pending:
            return
        batch = list(self._pending)
        self._pending.clear()
        self._ship_batch(batch, trace_id=trace_id or str(uuid.uuid4()))

    def _ship_batch(self, batch: list[CanonicalEntry], *, trace_id: str) -> None:
        batch_id = compute_batch_id(batch)
        payload = [e.to_wire() for e in batch]
        headers = {
            "X-API-Key": self.config.api_key,
            "X-Trace-Id": trace_id,
            "X-Batch-Id": batch_id,  # idempotent id (ADR-0084 §3)
            "Content-Type": "application/json",
        }

        attempt = 0
        while True:
            result = self.transport.post_ingestion(
                self.config.ingestion_url,
                payload,
                headers,
                self.config.request_timeout_seconds,
            )

            if result.status_code == 202:
                self.counters.batches_shipped_total += 1
                self.counters.entries_accepted_total += result.accepted
                self.counters.entries_rejected_total += result.rejected
                _log(
                    "batch_shipped",
                    batch_id=batch_id,
                    trace_id=trace_id,
                    entries=len(batch),
                    accepted=result.accepted,
                    rejected=result.rejected,
                    attempt=attempt,
                )
                return

            if result.status_code == 401:
                self.counters.auth_failures_total += 1
                _log("auth_failure", batch_id=batch_id, trace_id=trace_id, status=401)
                raise FailFastAuthError()

            if result.status_code == 422:
                # Bad batch: log + drop with a validation counter (ADR-0084 §4).
                self.counters.entries_rejected_total += len(batch)
                _log(
                    "batch_rejected_422",
                    batch_id=batch_id,
                    trace_id=trace_id,
                    entries=len(batch),
                )
                return

            if result.status_code in (429, 503):
                if attempt >= self.config.max_retries:
                    # Budget exhausted: COUNT the remainder as dropped — never
                    # silently discard (ENV-FR-04).
                    self.counters.entries_dropped_total += len(batch)
                    _log(
                        "batch_dropped_retry_exhausted",
                        batch_id=batch_id,
                        trace_id=trace_id,
                        entries=len(batch),
                        status=result.status_code,
                        attempts=attempt,
                    )
                    return
                delay = self._backoff_delay(attempt, result.retry_after_seconds)
                self.counters.retries_total += 1
                _log(
                    "batch_retry",
                    batch_id=batch_id,
                    trace_id=trace_id,
                    status=result.status_code,
                    attempt=attempt,
                    delay_seconds=round(delay, 3),
                )
                self.sleep(delay)
                attempt += 1
                continue

            # Any other non-2xx: bounded retry as well, then drop-and-count.
            if attempt >= self.config.max_retries:
                self.counters.entries_dropped_total += len(batch)
                _log(
                    "batch_dropped_unexpected_status",
                    batch_id=batch_id,
                    trace_id=trace_id,
                    entries=len(batch),
                    status=result.status_code,
                )
                return
            delay = self._backoff_delay(attempt, result.retry_after_seconds)
            self.counters.retries_total += 1
            self.sleep(delay)
            attempt += 1

    def _backoff_delay(self, attempt: int, retry_after: float | None) -> float:
        """Bounded exponential backoff; ``Retry-After`` wins when present.

        Capped at ``backoff_max_seconds`` so a hostile/large ``Retry-After``
        cannot stall the shipper unbounded (W1-6 spirit at the app layer).
        """
        cap: float = self.config.backoff_max_seconds
        if retry_after is not None and retry_after >= 0:
            return float(min(retry_after, cap))
        base: float = self.config.backoff_base_seconds * (2**attempt)
        return float(min(base, cap))
