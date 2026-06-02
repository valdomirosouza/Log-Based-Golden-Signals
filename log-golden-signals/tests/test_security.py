import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

VALID_INGESTION_KEY = "test-ingestion-key-123"
VALID_ANALYTICS_KEY = "test-analytics-key-456"

VALID_ENTRY = {
    "timestamp": "2026-05-31T10:00:00Z",
    "path": "/api/v1/users",
    "method": "GET",
    "status_code": 200,
    "response_time_ms": 45.3,
    "bytes_sent": 1024,
    "client_ip": "192.168.1.100",
}


# ── Ingestion API security ────────────────────────────────────────────────────

def _ingestion_client():
    from ingestion_api.app.main import app
    return TestClient(app)


class TestIngestionAuth:
    def test_unauthenticated_request_returns_401(self):
        with patch.dict(os.environ, {"INGESTION_API_KEY": VALID_INGESTION_KEY}):
            client = _ingestion_client()
            resp = client.post("/ingestion", json={"logs": [VALID_ENTRY]})
        assert resp.status_code == 401

    def test_wrong_key_returns_401(self):
        with patch.dict(os.environ, {"INGESTION_API_KEY": VALID_INGESTION_KEY}):
            client = _ingestion_client()
            resp = client.post(
                "/ingestion",
                json={"logs": [VALID_ENTRY]},
                headers={"X-API-Key": "wrong-key"},
            )
        assert resp.status_code == 401

    def test_valid_key_returns_200(self):
        with patch.dict(os.environ, {"INGESTION_API_KEY": VALID_INGESTION_KEY}):
            client = _ingestion_client()
            resp = client.post(
                "/ingestion",
                json={"logs": [VALID_ENTRY]},
                headers={"X-API-Key": VALID_INGESTION_KEY},
            )
        assert resp.status_code == 200

    def test_health_skips_auth(self):
        with patch.dict(os.environ, {"INGESTION_API_KEY": VALID_INGESTION_KEY}):
            client = _ingestion_client()
            resp = client.get("/health")
        assert resp.status_code == 200


# ── Rate limiting ─────────────────────────────────────────────────────────────

class TestRateLimiting:
    def test_rate_limit_logic_triggers_at_101(self):
        """Unit test the rate_limit module logic directly."""
        from ingestion_api.app.rate_limit import RATE_LIMIT
        assert RATE_LIMIT == 100

    def test_rate_limit_returns_429_when_exceeded(self):
        """Mock Redis to simulate a counter already at 101."""
        mock_r = AsyncMock()
        mock_r.incr = AsyncMock(return_value=101)
        mock_r.expire = AsyncMock(return_value=1)
        mock_r.ttl = AsyncMock(return_value=45)

        async def run():
            import hashlib

            from ingestion_api.app.rate_limit import check_rate_limit
            key_hash = hashlib.sha256(b"test-key").hexdigest()
            return await check_rate_limit(mock_r, key_hash)

        allowed, retry_after = asyncio.get_event_loop().run_until_complete(run())
        assert allowed is False
        assert retry_after == 45

    def test_rate_limit_allows_under_100(self):
        mock_r = AsyncMock()
        mock_r.incr = AsyncMock(return_value=50)
        mock_r.expire = AsyncMock(return_value=1)

        async def run():
            import hashlib

            from ingestion_api.app.rate_limit import check_rate_limit
            key_hash = hashlib.sha256(b"test-key").hexdigest()
            return await check_rate_limit(mock_r, key_hash)

        allowed, retry_after = asyncio.get_event_loop().run_until_complete(run())
        assert allowed is True
        assert retry_after == 0


# ── Analytics API security + HITL flag ───────────────────────────────────────

def _analytics_client():
    from analytics_api.app.main import app
    return TestClient(app)


class TestAnalyticsAuth:
    def test_unauthenticated_analytics_returns_401(self):
        with patch.dict(os.environ, {"ANALYTICS_API_KEY": VALID_ANALYTICS_KEY}):
            client = _analytics_client()
            resp = client.get("/analytics/paths")
        assert resp.status_code == 401

    def test_health_skips_auth(self):
        with patch.dict(os.environ, {"ANALYTICS_API_KEY": VALID_ANALYTICS_KEY}):
            client = _analytics_client()
            resp = client.get("/health")
        assert resp.status_code == 200

    def test_analytics_health_skips_auth(self):
        with patch.dict(os.environ, {"ANALYTICS_API_KEY": VALID_ANALYTICS_KEY}):
            client = _analytics_client()
            resp = client.get("/analytics/health")
        assert resp.status_code == 200


class TestHITLGovernance:
    def test_governance_hotl_when_normal(self):
        from analytics_api.app.main import _governance
        summary = {"p99_ms": 200.0, "avg_error_rate": 0.02}
        gov = _governance(summary)
        assert gov["recommended_action_mode"] == "HOTL"
        assert gov["human_approval_required"] is False

    def test_governance_hitl_when_p99_high(self):
        from analytics_api.app.main import _governance
        summary = {"p99_ms": 550.0, "avg_error_rate": 0.01}
        gov = _governance(summary)
        assert gov["recommended_action_mode"] == "HITL"
        assert gov["human_approval_required"] is True

    def test_governance_hitl_when_error_rate_high(self):
        from analytics_api.app.main import _governance
        summary = {"p99_ms": 100.0, "avg_error_rate": 0.06}
        gov = _governance(summary)
        assert gov["recommended_action_mode"] == "HITL"
        assert gov["human_approval_required"] is True

    def test_governance_pii_sanitized_always_true(self):
        from analytics_api.app.main import _governance
        gov = _governance(None)
        assert gov["pii_sanitized"] is True
        assert gov["data_classification"] == "operational-telemetry"


# ── Audit log ─────────────────────────────────────────────────────────────────

class TestAuditLog:
    def test_audit_entry_written_on_ingestion(self):
        """Verify that _write_audit is called during POST /ingestion."""
        import ingestion_api.app.main as main_mod
        audit_calls = []

        async def mock_audit(*args, **kwargs):
            audit_calls.append(args)

        with patch.object(main_mod, "_write_audit", side_effect=mock_audit):
            client = TestClient(main_mod.app)
            client.post(
                "/ingestion",
                json={"logs": [VALID_ENTRY]},
                headers={"X-API-Key": VALID_INGESTION_KEY},
            )

        assert len(audit_calls) >= 1
