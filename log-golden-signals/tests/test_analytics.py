import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


# ── Percentile computation ────────────────────────────────────────────────────

class TestPercentiles:
    def test_p50_known_dataset(self):
        """10 values [10..100]: P50 = 55.0"""
        from analytics_api.app.percentiles import percentile
        values = [float(i * 10) for i in range(1, 11)]  # [10,20,...,100]
        assert percentile(values, 50) == pytest.approx(55.0)

    def test_p95_known_dataset(self):
        """10 values [10..100]: P95 = 95.5"""
        from analytics_api.app.percentiles import percentile
        values = [float(i * 10) for i in range(1, 11)]
        assert percentile(values, 95) == pytest.approx(95.5)

    def test_p99_known_dataset(self):
        """10 values [10..100]: P99 = 99.1"""
        from analytics_api.app.percentiles import percentile
        values = [float(i * 10) for i in range(1, 11)]
        assert percentile(values, 99) == pytest.approx(99.1)

    def test_single_value(self):
        from analytics_api.app.percentiles import percentile
        assert percentile([42.0], 50) == 42.0
        assert percentile([42.0], 99) == 42.0

    def test_empty_returns_none(self):
        from analytics_api.app.percentiles import percentile
        assert percentile([], 50) is None


# ── Analytics API endpoint tests ──────────────────────────────────────────────

def _make_mock_redis():
    r = AsyncMock()
    r.ping = AsyncMock(return_value=True)
    r.scard = AsyncMock(return_value=3)
    r.smembers = AsyncMock(return_value={"/api/v1/users", "/api/v1/orders", "/health"})
    # zrange returns list of (member, score) tuples for latency
    r.zrange = AsyncMock(return_value=[(f"v:{i}", float(i * 10)) for i in range(1, 11)])
    r.get = AsyncMock(return_value="100")
    return r


class TestAnalyticsAPI:
    def setup_method(self):
        from analytics_api.app.main import app
        from analytics_api.app import redis_client
        self.app = app
        self.redis_client = redis_client

    def _patch_redis(self, mock_r):
        """Patch get_redis in both the main module and the redis_client module."""
        from unittest.mock import patch
        import analytics_api.app.main as main_mod
        return patch.object(main_mod, "get_redis", AsyncMock(return_value=mock_r))

    def test_empty_result_returns_null_summary(self):
        mock_r = AsyncMock()
        mock_r.ping = AsyncMock(return_value=True)
        mock_r.zrange = AsyncMock(return_value=[])
        mock_r.get = AsyncMock(return_value=None)

        with self._patch_redis(mock_r):
            client = TestClient(self.app)
            resp = client.get(
                "/analytics",
                params={
                    "path": "/nonexistent",
                    "signal": "latency",
                    "window": "1m",
                    "from": "2026-05-31T10:00:00Z",
                    "to": "2026-05-31T10:05:00Z",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["buckets"] == []
        assert body["summary"] is None

    def test_invalid_signal_returns_422(self):
        client = TestClient(self.app)
        resp = client.get(
            "/analytics",
            params={
                "path": "/api/v1/users",
                "signal": "unknown_signal",
                "window": "1m",
            },
        )
        assert resp.status_code == 422

    def test_invalid_window_returns_422(self):
        client = TestClient(self.app)
        resp = client.get(
            "/analytics",
            params={
                "path": "/api/v1/users",
                "signal": "latency",
                "window": "10m",
            },
        )
        assert resp.status_code == 422

    def test_latency_response_has_percentile_fields(self):
        mock_r = _make_mock_redis()
        with self._patch_redis(mock_r):
            client = TestClient(self.app)
            resp = client.get(
                "/analytics",
                params={
                    "path": "/api/v1/users",
                    "signal": "latency",
                    "window": "1m",
                    "from": "2026-05-31T10:00:00Z",
                    "to": "2026-05-31T10:05:00Z",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        if body["buckets"]:
            bucket = body["buckets"][0]
            assert "p50_ms" in bucket
            assert "p95_ms" in bucket
            assert "p99_ms" in bucket
            assert isinstance(bucket["p50_ms"], float)

    def test_analytics_paths_returns_list(self):
        mock_r = _make_mock_redis()
        with self._patch_redis(mock_r):
            client = TestClient(self.app)
            resp = client.get("/analytics/paths")
        assert resp.status_code == 200
        body = resp.json()
        assert "paths" in body
        assert isinstance(body["paths"], list)

    def test_analytics_health(self):
        mock_r = _make_mock_redis()
        import analytics_api.app.main as main_mod
        with patch.object(main_mod, "get_redis", AsyncMock(return_value=mock_r)):
            with patch.object(main_mod, "is_connected", AsyncMock(return_value=True)):
                client = TestClient(self.app)
                resp = client.get("/analytics/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "redis_connected" in body
        assert "tracked_paths" in body


class TestPercentileEdgeCases:
    def test_p0_returns_minimum(self):
        """P0 must return the smallest value."""
        from analytics_api.app.percentiles import percentile
        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        assert percentile(values, 0) == pytest.approx(10.0)

    def test_p100_returns_maximum(self):
        """P100 must return the largest value."""
        from analytics_api.app.percentiles import percentile
        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        assert percentile(values, 100) == pytest.approx(50.0)

    def test_all_identical_values(self):
        """All same values — every percentile returns that value."""
        from analytics_api.app.percentiles import percentile
        values = [42.0] * 10
        assert percentile(values, 50) == pytest.approx(42.0)
        assert percentile(values, 99) == pytest.approx(42.0)

    def test_buckets_range_single_point(self):
        """from_ts == to_ts should return exactly one bucket (no crash)."""
        from analytics_api.app.query import _buckets_for_range
        buckets = _buckets_for_range(1748685600.0, 1748685600.0, "1m")
        assert len(buckets) == 1
        assert buckets[0] == 1748685600
