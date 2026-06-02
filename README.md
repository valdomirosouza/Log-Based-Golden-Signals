# Log-Based Golden Signals

> **Research project** · [`log-golden-signals/`](log-golden-signals/) · _Valdomiro de Oliveira Souza Jr., PPGCA/Unisinos, 2026_

A production-grade observability infrastructure that ingests HAProxy access logs, extracts the four **Golden Signals** (traffic, latency, error, saturation), aggregates them into time-series windows, and exposes predictive analytics (P50/P95/P99 percentiles) via a REST API — designed as the data foundation for an **Agentic AI Copilot** capable of reducing MTTD and MTTR during incident response.

---

## Architecture

```
HAProxy instances
      │  POST /ingestion  (JSON batch)
      ▼
┌─────────────────────┐
│   Ingestion API     │  :8000  FastAPI + uvicorn
│                     │  – JSON schema validation (Pydantic v2)
│                     │  – PII masking (IPv4 last-octet, IPv6 last-80-bit)
│                     │  – Golden Signal extraction per log entry
│                     │  – Publishes to Redis Stream (XADD)
└────────┬────────────┘
         │  golden-signals:events  (Redis Stream)
         ▼
┌──────────────────────┐
│  Metrics Processor   │  :8002  asyncio worker
│                      │  – XREADGROUP consumer (ack + delete)
│                      │  – 1-min / 5-min window aggregation
│                      │  – Latency: ZADD sorted sets
│                      │  – Traffic/Errors: INCR counters
│                      │  – Saturation: INCRBYFLOAT
│                      │  – Configurable TTL per window
│                      │  – Exponential back-off reconnect + DLQ
└────────┬─────────────┘
         │  Redis 7  (internal :6379)
         ▼
┌──────────────────────┐
│   Analytics API      │  :8001  FastAPI + uvicorn
│                      │  – P50 / P95 / P99 (rank-based interpolation)
│                      │  – Error rate, traffic volume, saturation %
│                      │  – HITL/HOTL governance metadata per response
│                      │  – Audit log stream (GET /audit)
└──────────────────────┘
```

---

## Quick Start

```bash
cd log-golden-signals

make build                  # build all Docker images
make up                     # start stack (haproxy · ingestion · processor · analytics · redis)
make seed                   # ingest 1,000 synthetic HAProxy log entries
make test                   # 53 unit tests (no Docker required)
make test-integration       # full pipeline: seed → aggregate → percentile assertions
make down                   # tear down
```

---

## Services

| Service             | Port            | Role                                      |
| ------------------- | --------------- | ----------------------------------------- |
| `haproxy`           | 80              | Traffic proxy, log source                 |
| `ingestion_api`     | 8000            | Receive, validate, sanitize, enqueue      |
| `metrics_processor` | 8002            | Aggregate windows, persist to Redis       |
| `redis`             | 6379 (internal) | Time-series store + stream queue          |
| `analytics_api`     | 8001            | Percentile computation, agentic interface |

---

## Analytics API — Example Query

```bash
curl "http://localhost:8001/analytics?path=/api/v1/users&signal=latency&window=1m"
```

```json
{
  "path": "/api/v1/users",
  "signal": "latency",
  "window": "1m",
  "buckets": [
    {
      "epoch_bucket": 1748685600,
      "p50_ms": 63.5,
      "p95_ms": 187.1,
      "p99_ms": 412.8,
      "count": 340,
      "error_rate": 0.012,
      "saturation_pct": 0.23
    }
  ],
  "summary": {
    "p50_ms": 44.1,
    "p95_ms": 192.0,
    "p99_ms": 430.5,
    "total_requests": 2040,
    "avg_error_rate": 0.011
  },
  "_governance": {
    "data_classification": "operational-telemetry",
    "pii_sanitized": true,
    "recommended_action_mode": "HOTL",
    "human_approval_required": false
  }
}
```

When `p99_ms >= 500` or `error_rate >= 0.05`, the `_governance` block automatically switches to `"recommended_action_mode": "HITL"` — signalling the Agentic Copilot to request human approval before acting.

---

## Security Controls

| Control       | Implementation                                                           |
| ------------- | ------------------------------------------------------------------------ |
| API Key auth  | `X-API-Key` header; `INGESTION_API_KEY` / `ANALYTICS_API_KEY` env vars   |
| Rate limiting | 100 req/min per key via Redis `INCR` + `EXPIRE`; `429 Retry-After`       |
| PII masking   | IPv4 last-octet → `xxx`; IPv6 last 80 bits zeroed before any storage     |
| Audit log     | Every API call → `XADD golden-signals:audit`; queryable via `GET /audit` |

---

Full documentation: [`log-golden-signals/README.md`](log-golden-signals/README.md)
