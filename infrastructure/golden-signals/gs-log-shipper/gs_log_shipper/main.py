"""Entrypoint for gs-log-shipper.

Receives pinned HAProxy access-log lines over a **TCP syslog listener** (the
runtime transport edge — ADR-0084 Amendment 2026-06-17, Refs #28), strips the
syslog envelope, then parses → normalises → batches → ships them at-least-once
to ``POST /ingestion`` (ADR-0084). HAProxy has no native file logging; its
first-class sink is syslog, so the shipper listens for syslog frames rather than
reading stdin (the original stdin design EOF-looped because the compose topology
never connected HAProxy's stdout to the shipper's stdin — Defect B, issue #28).

The :func:`run` iterator-loop below remains the unit-testable parse→batch→flush
driver (with a time-based flush); :func:`main` wires the real TCP syslog listener
(:mod:`syslog_listener`) and the real :class:`HttpxTransport`. The batching/retry/
PII logic lives in :mod:`shipper` and stays network-free and unit-tested.

Refs: SPEC-LGS-002 §1.1/§7/§8, ADR-0084 (Amendment 2026-06-17). Issue #28, #18, #24.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from .config import ShipperConfig
from .shipper import Shipper
from .syslog_listener import serve
from .transport import HttpxTransport


def run(
    config: ShipperConfig,
    shipper: Shipper,
    lines: Iterator[str],
    *,
    now: object = time.monotonic,
) -> None:
    """Drive the parse → batch → ship loop with a time-based flush.

    Flushes when the batch fills (``offer_line`` returns True) or when
    ``batch_max_seconds`` elapses since the oldest buffered entry. On stream end
    a final flush ships any remainder (never silently dropped — ENV-FR-04). This
    function is the unit-testable core of the listener's per-line handling.
    """
    clock = now if callable(now) else time.monotonic
    last_flush = clock()
    for line in lines:
        full = shipper.offer_line(line)
        due = (clock() - last_flush) >= config.batch_max_seconds
        if full or (shipper.pending_count and due):
            shipper.flush()
            last_flush = clock()
    shipper.flush()


def main() -> int:  # pragma: no cover - process entrypoint (blocking listener)
    """CLI entrypoint: build config + transport and serve the syslog listener."""
    config = ShipperConfig.from_env()
    shipper = Shipper(config=config, transport=HttpxTransport())
    serve(config, shipper)
    return 0


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    raise SystemExit(main())
