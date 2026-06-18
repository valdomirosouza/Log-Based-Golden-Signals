"""golden-signals STUB — contract-shaped responder for the demo environment.

The real `golden-signals` service is a Java/Spring Boot black box (ADR-0066) and
HAS NO RUNNABLE IMAGE YET (Phase-0 carry-over; SPEC-LGS-002 §13/§15-Q1, ADR-0085
§8). To let the compose environment wire up and the contract paths be exercised,
this stub honours **only** the SPEC-LGS-001 §8 HTTP *contract shape* — it does
NOT implement any application logic (no percentile maths, no masking, no
windowing — those belong to SPEC-LGS-001; CLAUDE.md §3.4 / out-of-scope).

Contract honoured (grounded against services/golden-signals .../IngestionController,
LogEntryDto, HealthResponse):

* ``POST /ingestion``   — ``X-API-Key`` required → ``202 {"accepted","rejected"}``;
                          missing/invalid key → ``401``; non-array / bad JSON →
                          ``422``; optional forced ``429``/``503`` via env so the
                          shipper's Retry-After path is demonstrable.
* ``GET /analytics/health`` — no auth → ``200 {"status","store_connected",
                          "tracked_paths"}``; forced ``503`` via env.
* ``GET /analytics``, ``GET /analytics/paths`` — minimal contract-shaped 200/401.

Live `gs-demo` acceptance (AC-01/AC-10) against the real Java image stays
DEFERRED-AND-LOGGED until ADR-0066's image exists. Refs #18, #26.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_KEY_HEADER = "X-API-Key"  # grounded: ApiKeyAuthFilter.API_KEY_HEADER


def _valid_keys() -> set[str]:
    raw = os.environ.get("GS_API_KEYS", "").strip()
    return {k.strip() for k in raw.split(",") if k.strip()}


def _force_status(env_name: str) -> int | None:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


class StubHandler(BaseHTTPRequestHandler):
    """Contract-shaped handler. No application logic (CLAUDE.md §3.4)."""

    server_version = "golden-signals-stub/0"

    def log_message(self, fmt: str, *args: object) -> None:
        # Quiet by default; never echo headers/body (could carry a client IP).
        return

    def _send_json(self, status: int, body: dict[str, object]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if status == 429:
            self.send_header("Retry-After", os.environ.get("GS_STUB_RETRY_AFTER", "1"))
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _authed(self) -> bool:
        keys = _valid_keys()
        if not keys:
            return True  # no keys configured → accept (dev convenience only)
        return self.headers.get(API_KEY_HEADER, "") in keys

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/analytics/health":
            forced = _force_status("GS_STUB_HEALTH_STATUS")
            if forced == 503:
                self._send_json(503, {"status": "degraded", "store_connected": False})
                return
            self._send_json(200, {"status": "ok", "store_connected": True, "tracked_paths": 0})
            return
        if path in ("/analytics", "/analytics/paths"):
            if not self._authed():
                self._send_json(401, {"error": "invalid api key"})
                return
            body: dict[str, object] = (
                {"paths": []} if path == "/analytics/paths" else {"buckets": [], "_governance": {}}
            )
            self._send_json(200, body)
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path != "/ingestion":
            self._send_json(404, {"error": "not found"})
            return
        if not self._authed():
            self._send_json(401, {"error": "invalid api key"})
            return

        forced = _force_status("GS_STUB_INGESTION_STATUS")
        if forced in (429, 503):
            self._send_json(forced, {"error": "backpressure"})
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        try:
            parsed = json.loads(raw or b"null")
        except json.JSONDecodeError:
            self._send_json(422, {"error": "malformed json"})
            return
        if not isinstance(parsed, list):
            self._send_json(422, {"error": "body must be a JSON array (FR-01)"})
            return
        # Contract shape only: count accepted; reject entries missing a required
        # key. NO signal extraction / persistence (SPEC-LGS-001 owns that).
        required = {"path", "statusCode", "responseTimeMs", "bytesSent", "timestamp"}
        accepted = sum(1 for e in parsed if isinstance(e, dict) and required <= set(e))
        rejected = len(parsed) - accepted
        self._send_json(202, {"accepted": accepted, "rejected": rejected})


def main() -> int:
    host = os.environ.get("GS_STUB_HOST", "0.0.0.0")  # noqa: S104 - container-internal bind
    port = int(os.environ.get("GS_STUB_PORT", "8085"))
    server = ThreadingHTTPServer((host, port), StubHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        server.shutdown()
    return 0


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    raise SystemExit(main())
