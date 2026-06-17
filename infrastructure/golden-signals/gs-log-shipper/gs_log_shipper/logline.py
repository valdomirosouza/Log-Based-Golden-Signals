"""Parse a pinned HAProxy access-log line into the canonical ingestion entry.

The HAProxy `log-format` is pinned in ``infrastructure/golden-signals/haproxy/
haproxy.cfg`` (ADR-0084 §2) to emit the ADR-0068 fields the four Golden Signals
need. To keep parsing unambiguous and robust against the spaces/brackets in a
default HAProxy line, the pinned format emits **tab-separated key=value pairs**
with a fixed leading sentinel ``GSLOG\\t``:

    GSLOG\\tts=<ms>\\tmethod=<m>\\tpath=<p>\\tstatus=<code>\\tTr=<ms>\\tTt=<ms>
         \\tbytes=<n>\\tci=<ip>\\tbackend=<name>

Mapping onto the SPEC-LGS-001 §9.1 canonical entry (verified against the live
Java DTO ``LogEntryDto`` — the wire contract is **camelCase**):

    path -> path                 (passed through faithfully, ADR-0084 §2)
    method -> (context only; the §9.1 DTO has no method field)
    status -> statusCode (int)
    Tr (%Tr server response) -> responseTimeMs (float)   <-- the latency signal
    Tt (%Tt total)           -> context only, not the signal (ADR-0084 §2/Q4)
    bytes (%B) -> bytesSent (int)
    ci (%ci)   -> clientIp (str, telemetry-L2 PII; masked by service, never
                  logged raw by the shipper — ENV-NFR-04)
    ts -> timestamp (epoch-millis int)
    backend (%b) -> context only (optional, not in the §9.1 DTO)

A line that is missing a required field, has a malformed value, or exceeds the
configured size cap is **dropped with a counter**, never crashes the shipper
(§8, ADR-0084 §4). Refs: SPEC-LGS-002 §9.1, ADR-0084, ADR-0068. Issue #24.
"""

from __future__ import annotations

from dataclasses import dataclass

SENTINEL = "GSLOG"

# Required keys that must be present and well-formed for an entry to be shipped.
_REQUIRED = ("ts", "path", "status", "Tr", "bytes")


@dataclass(frozen=True)
class CanonicalEntry:
    """One normalised ingestion entry in the SPEC-LGS-001 §9.1 wire shape.

    Field names match the live Java ``LogEntryDto`` (camelCase). ``client_ip``
    is held only for transit on the internal network; it is **never** written
    to the shipper's own stdout (ENV-NFR-04) and is masked by the service
    before any persist/log (FR-02).
    """

    timestamp: int
    path: str
    status_code: int
    response_time_ms: float
    bytes_sent: int
    client_ip: str | None

    def to_wire(self) -> dict[str, object]:
        """Render the camelCase JSON object the `/ingestion` contract accepts.

        ``clientIp`` is omitted when absent rather than sent as null, matching
        the DTO's optional field. ``method``/``backendName``/``Tt`` are context
        fields and deliberately not part of the §9.1 wire entry.
        """
        wire: dict[str, object] = {
            "path": self.path,
            "statusCode": self.status_code,
            "responseTimeMs": self.response_time_ms,
            "bytesSent": self.bytes_sent,
            "timestamp": self.timestamp,
        }
        if self.client_ip:
            wire["clientIp"] = self.client_ip
        return wire


class ParseError(ValueError):
    """Raised when a HAProxy line cannot be parsed into a CanonicalEntry."""


def parse_line(line: str, *, max_line_bytes: int = 8192) -> CanonicalEntry:
    """Parse one pinned HAProxy access-log line into a :class:`CanonicalEntry`.

    Validates untrusted input at the boundary (CLAUDE.md §3.2): an oversized,
    malformed, or incomplete line raises :class:`ParseError` so the caller can
    drop-and-count it (never crash, never silently discard — ADR-0084 §4).
    """
    if line is None:  # pragma: no cover - defensive
        raise ParseError("line is None")
    # Bound the input before doing any work (oversized-line guard, §3.2).
    if len(line.encode("utf-8", errors="replace")) > max_line_bytes:
        raise ParseError(f"line exceeds max_line_bytes={max_line_bytes}")

    stripped = line.strip()
    if not stripped:
        raise ParseError("empty line")

    parts = stripped.split("\t")
    if parts[0] != SENTINEL:
        raise ParseError("missing GSLOG sentinel — not a pinned-format line")

    fields: dict[str, str] = {}
    for token in parts[1:]:
        key, sep, value = token.partition("=")
        if not sep:
            # A token without '=' is malformed; skip it (required-key check
            # below will fail the line if a needed field is thereby missing).
            continue
        fields[key] = value

    missing = [k for k in _REQUIRED if k not in fields or fields[k] == ""]
    if missing:
        raise ParseError(f"missing required field(s): {','.join(missing)}")

    try:
        timestamp = int(fields["ts"])
        status_code = int(fields["status"])
        response_time_ms = float(fields["Tr"])
        bytes_sent = int(fields["bytes"])
    except ValueError as exc:
        raise ParseError(f"non-numeric field: {exc}") from exc

    # HAProxy emits -1 for a timing field when "the event did not occur" — for
    # %Tr this means there was no server-side request/response (an applet or
    # `http-request return` canned response has no upstream server, so server
    # response time is undefined). That is HAProxy's documented sentinel, NOT a
    # corrupt line: normalise it to 0.0 ms (a canned/applet response has no
    # measurable server-side processing) rather than dropping the entry. The
    # latency signal stays %Tr per ADR-0084 §2; %Tt remains context-only.
    # Refs: ADR-0084 (Amendment 2026-06-17), issue #28.
    if response_time_ms == -1.0:
        response_time_ms = 0.0

    if timestamp < 0 or bytes_sent < 0 or response_time_ms < 0:
        raise ParseError("negative numeric field")
    if not (100 <= status_code <= 599):
        raise ParseError(f"status out of range: {status_code}")

    path = fields["path"]
    if not path:
        raise ParseError("empty path")

    # client_ip ('-' is HAProxy's "no value" sentinel; treat as absent).
    raw_ci = fields.get("ci", "")
    client_ip = raw_ci if raw_ci and raw_ci != "-" else None

    return CanonicalEntry(
        timestamp=timestamp,
        path=path,
        status_code=status_code,
        response_time_ms=response_time_ms,
        bytes_sent=bytes_sent,
        client_ip=client_ip,
    )
