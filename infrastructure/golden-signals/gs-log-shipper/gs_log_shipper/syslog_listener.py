"""TCP syslog listener for gs-log-shipper (ADR-0084 Amendment 2026-06-17).

HAProxy has no native file logging — its first-class log sink is **syslog**.
The compose topology therefore wires ``HAProxy --(syslog/TCP over gs-net)-->
gs-log-shipper``. This module is the runtime transport edge: it accepts a TCP
syslog stream, frames it into individual messages, strips the syslog envelope
(RFC 3164 ``<PRI>`` + optional prefix) to recover the pinned ``GSLOG\\t…``
access-log line, and hands each recovered line to the EXISTING shipper pipeline
(``Shipper.offer_line`` → batch → ``POST /ingestion`` → bounded retry). The
parse/normalise/batch/retry/PII guarantees in :mod:`shipper` and :mod:`logline`
are unchanged — this module only changes the *first hop* from stdin to a socket.

Transport is **TCP** (not UDP): UDP silently drops datagrams under pressure,
which violates ENV-FR-04 ("never silently discard") and the
``shipper_delivery_ratio >= 99.9%`` SLI. A drop on the first hop is invisible
loss no downstream counter can attribute — strictly worse than the bounded,
*counted* drop the retry budget already accepts (ADR-0084 Amendment, decision 9).

Untrusted-input posture (CLAUDE.md §3.2): the syslog frame is untrusted. An
oversized or malformed frame is treated exactly like a malformed access-log
line — it flows into ``offer_line``, which drops it with a ``parse_errors``
counter and never crashes. The listener itself never logs a raw frame (it may
carry a ``client_ip`` — ENV-NFR-04); it logs only counts and the listener
lifecycle.

Refs: SPEC-LGS-002 §1.1/§7/§8, ADR-0084 (Amendment 2026-06-17), ADR-0085.
Issue #28 (Defect B), #18 (epic).
"""

from __future__ import annotations

import json
import socketserver
import sys
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import ShipperConfig
    from .shipper import Shipper

# A syslog frame begins with an RFC 3164 priority value "<NNN>" (0..191). We
# strip everything up to and including the '>' so the recovered payload starts
# at the application message (the pinned "GSLOG\t…" line). HAProxy prepends the
# PRI to the configured log-format string.
_MAX_FRAME_BYTES = 65536  # hard upper bound per frame before we drop it


def strip_syslog_envelope(frame: str) -> str:
    """Strip the leading RFC 3164 ``<PRI>`` from one syslog frame.

    HAProxy emits ``<PRI>`` immediately followed by the configured
    ``log-format`` string (our pinned ``GSLOG\\t…`` line — HAProxy's default
    syslog header is suppressed by the raw-ish pinned format, but we defend
    against a leading timestamp/host header too by anchoring on the ``GSLOG``
    sentinel when a ``<PRI>`` alone does not expose it).

    The function is deliberately lenient: anything it cannot confidently strip
    is returned as-is so the downstream parser (which anchors on the ``GSLOG``
    sentinel) makes the final accept/drop decision. It never raises.
    """
    text = frame.strip()
    if not text:
        return text
    # 1) Drop a leading "<PRI>" priority token if present.
    if text.startswith("<"):
        end = text.find(">")
        if 0 < end <= 4:  # "<0>".."<191>"
            text = text[end + 1 :]
    # 2) If the message still doesn't start with the sentinel, try to anchor on
    #    it directly (defends against an interposed timestamp/hostname header
    #    that some syslog stacks prepend). If the sentinel is absent we return
    #    what we have and let parse_line drop it with a counter.
    if not text.startswith("GSLOG"):
        idx = text.find("GSLOG")
        if idx != -1:
            text = text[idx:]
    return text


def _log(event: str, **fields: object) -> None:
    """Emit one structured JSON lifecycle line (ENV-NFR-03); never a raw frame."""
    record = {"ts": int(time.time() * 1000), "component": "gs-log-shipper", "event": event}
    record.update(fields)
    sys.stdout.write(json.dumps(record, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def serve(config: ShipperConfig, shipper: Shipper) -> None:  # pragma: no cover - blocking loop
    """Bind the TCP syslog listener and serve forever, feeding ``shipper``.

    Each newline-delimited frame on a connection is enveloped-stripped and
    offered to the shipper; a full batch flushes immediately, and an idle
    connection is flushed on close so a low-traffic tail still ships within the
    pipeline's guarantees. Bound on ``gs-net`` only (ENV-FR-12) — never
    host-published. This blocking loop is exercised end-to-end in live
    integration rather than unit-tested; the frame/handling logic it delegates
    to (:func:`strip_syslog_envelope`, :meth:`handle_frame`) is unit-tested.
    """
    handler = _make_handler(config, shipper)
    with _ThreadingTCPServer((config.syslog_host, config.syslog_port), handler) as server:
        _log(
            "syslog_listener_started",
            host=config.syslog_host,
            port=config.syslog_port,
            transport="tcp",
        )
        try:
            server.serve_forever()
        finally:
            shipper.flush()
            _log("syslog_listener_stopped")


class _ThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def handle_frame(frame: str, shipper: Shipper) -> None:
    """Strip one syslog frame's envelope and offer the line to the shipper.

    Pure-ish seam (no socket) so the syslog→canonical path is unit-testable.
    An oversized frame is dropped-and-counted at the shipper boundary; this
    function never raises and never logs the raw frame.
    """
    line = strip_syslog_envelope(frame)
    if not line:
        return
    if shipper.offer_line(line):
        shipper.flush()


def _make_handler(
    config: ShipperConfig, shipper: Shipper
) -> type[socketserver.StreamRequestHandler]:
    class _Handler(socketserver.StreamRequestHandler):  # pragma: no cover - needs a socket
        def handle(self) -> None:
            for raw in self.rfile:
                if len(raw) > _MAX_FRAME_BYTES:
                    # Oversized frame: drop-and-count at the parser boundary by
                    # offering a truncated sentinel-less marker is wrong; instead
                    # offer the raw (decoded) line so offer_line's own size guard
                    # counts it. We bound the decode to avoid unbounded memory.
                    raw = raw[:_MAX_FRAME_BYTES]
                frame = raw.decode("utf-8", errors="replace")
                handle_frame(frame, shipper)
            # Connection closed: flush any buffered remainder (never silent).
            shipper.flush()

    return _Handler
