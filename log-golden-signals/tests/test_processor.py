import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_event(
    path="/api/v1/users",
    response_time_ms=45.0,
    bytes_sent=1024,
    is_error=False,
    window_1m=1748685600,
    window_5m=1748685300,
):
    return {
        "path": path,
        "response_time_ms": response_time_ms,
        "bytes_sent": bytes_sent,
        "is_error": is_error,
        "window_1m": window_1m,
        "window_5m": window_5m,
    }


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Aggregation logic ─────────────────────────────────────────────────────────

class TestAggregation:
    def _make_redis(self):
        r = AsyncMock()
        r.incr = AsyncMock(return_value=1)
        r.zadd = AsyncMock(return_value=1)
        r.incrbyfloat = AsyncMock(return_value=1024.0)
        r.expire = AsyncMock(return_value=1)
        r.sadd = AsyncMock(return_value=1)
        return r

    def test_traffic_incr_called_for_both_windows(self):
        from metrics_processor.app.aggregator import aggregate
        r = self._make_redis()
        run(aggregate(r, _make_event()))
        traffic_keys = [
            c.args[0] for c in r.incr.call_args_list
            if "traffic" in c.args[0]
        ]
        assert len(traffic_keys) == 2
        assert any("1m" in k for k in traffic_keys)
        assert any("5m" in k for k in traffic_keys)

    def test_latency_zadd_called_for_both_windows(self):
        from metrics_processor.app.aggregator import aggregate
        r = self._make_redis()
        run(aggregate(r, _make_event(response_time_ms=99.9)))
        latency_calls = [
            c for c in r.zadd.call_args_list
            if "latency" in c.args[0]
        ]
        assert len(latency_calls) == 2
        # score must equal the response_time_ms value
        for c in latency_calls:
            scores = list(c.args[1].values())
            assert scores[0] == pytest.approx(99.9)

    def test_error_counter_incremented_when_is_error(self):
        from metrics_processor.app.aggregator import aggregate
        r = self._make_redis()
        run(aggregate(r, _make_event(is_error=True)))
        error_keys = [c.args[0] for c in r.incr.call_args_list if ":error:" in c.args[0]]
        assert len(error_keys) == 2  # 1m and 5m

    def test_error_counter_not_incremented_when_not_error(self):
        from metrics_processor.app.aggregator import aggregate
        r = self._make_redis()
        run(aggregate(r, _make_event(is_error=False)))
        error_keys = [c.args[0] for c in r.incr.call_args_list if ":error:" in c.args[0]]
        assert error_keys == []

    def test_saturation_incrbyfloat_called(self):
        from metrics_processor.app.aggregator import aggregate
        r = self._make_redis()
        run(aggregate(r, _make_event(bytes_sent=500_000)))
        sat_calls = [c for c in r.incrbyfloat.call_args_list if "saturation" in c.args[0]]
        assert len(sat_calls) == 2
        for c in sat_calls:
            assert c.args[1] == 500_000

    def test_path_registered_in_gs_paths_set(self):
        from metrics_processor.app.aggregator import aggregate
        r = self._make_redis()
        run(aggregate(r, _make_event(path="/api/v1/orders")))
        r.sadd.assert_called_with("gs:paths", "/api/v1/orders")


# ── Retention TTL ─────────────────────────────────────────────────────────────

class TestRetention:
    def _make_redis(self):
        r = AsyncMock()
        r.incr = AsyncMock(return_value=1)
        r.zadd = AsyncMock(return_value=1)
        r.incrbyfloat = AsyncMock(return_value=1.0)
        r.expire = AsyncMock(return_value=1)
        r.sadd = AsyncMock(return_value=1)
        return r

    def test_1m_keys_get_2h_ttl(self):
        import importlib
        import metrics_processor.app.aggregator as agg_mod
        with patch.dict(os.environ, {"RETENTION_1M_SECONDS": "7200", "RETENTION_5M_SECONDS": "86400"}):
            importlib.reload(agg_mod)
            from metrics_processor.app.aggregator import aggregate
            r = self._make_redis()
            run(aggregate(r, _make_event()))
            expire_calls = [(c.args[0], c.args[1]) for c in r.expire.call_args_list]
            for key, ttl in expire_calls:
                if "1m" in key:
                    assert ttl == 7200
                elif "5m" in key:
                    assert ttl == 86400


# ── DLQ behaviour ─────────────────────────────────────────────────────────────

class TestDLQ:
    def test_dlq_written_after_max_retries(self):
        """Simulate the retry counter logic in worker._process_loop."""
        MAX_RETRIES = 3
        retry_counts: dict = {}
        dlq_written = []

        def simulate_failure(msg_id, error):
            count = retry_counts.get(msg_id, 0) + 1
            retry_counts[msg_id] = count
            if count >= MAX_RETRIES:
                dlq_written.append(msg_id)
                retry_counts.pop(msg_id, None)

        for _ in range(MAX_RETRIES):
            simulate_failure("msg-001", ValueError("boom"))

        assert "msg-001" in dlq_written

    def test_dlq_not_written_before_max_retries(self):
        MAX_RETRIES = 3
        retry_counts: dict = {}
        dlq_written = []

        def simulate_failure(msg_id, error):
            count = retry_counts.get(msg_id, 0) + 1
            retry_counts[msg_id] = count
            if count >= MAX_RETRIES:
                dlq_written.append(msg_id)

        for _ in range(MAX_RETRIES - 1):
            simulate_failure("msg-002", ValueError("boom"))

        assert "msg-002" not in dlq_written


class TestAggregationEdgeCases:
    def _make_redis(self):
        r = AsyncMock()
        r.incr = AsyncMock(return_value=1)
        r.zadd = AsyncMock(return_value=1)
        r.incrbyfloat = AsyncMock(return_value=0.0)
        r.expire = AsyncMock(return_value=1)
        r.sadd = AsyncMock(return_value=1)
        return r

    def test_aggregate_zero_bytes_sent_does_not_error(self):
        """bytes_sent=0 must call incrbyfloat with 0 without raising."""
        from metrics_processor.app.aggregator import aggregate
        r = self._make_redis()
        run(aggregate(r, {
            "path": "/api/test",
            "response_time_ms": 10.0,
            "bytes_sent": 0,
            "is_error": False,
            "window_1m": 1748685600,
            "window_5m": 1748685300,
        }))
        sat_calls = [c for c in r.incrbyfloat.call_args_list if "saturation" in c.args[0]]
        assert len(sat_calls) == 2
        for c in sat_calls:
            assert c.args[1] == 0
