# Log-Based Golden Signals

> A scalable ingestion and predictive analytics infrastructure for Agentic AI Copilots.
> Based on the paper _"Log-Based Golden Signals: A Scalable Ingestion and Predictive Analytics Infrastructure for Agentic AI Copilots"_ — Valdomiro de Oliveira Souza Jr., PPGCA/Unisinos, 2026.

---

## Architecture

```
HAProxy instances
      │  POST /ingestion  (JSON batch)
      ▼
┌─────────────────────┐
│   Ingestion API     │  :8000
│   FastAPI + uvicorn │  – validates JSON schema
│                     │  – masks PII (IP masking)
│                     │  – extracts Golden Signals
│                     │  – publishes to Redis Stream
└────────┬────────────┘
         │  XADD  golden-signals:events
         ▼
┌──────────────────────┐
│  Metrics Processor   │  :8002
│  asyncio worker      │  – XREADGROUP consumer
│                      │  – 1m / 5m window aggregation
│                      │  – persists to Redis
│                      │  – GET /metrics  (lag, processed, dlq)
└────────┬─────────────┘
         │  ZADD / INCR / INCRBYFLOAT
         ▼
┌──────────────────────┐
│   Redis 7            │  :6379  (internal)
│   Time-Series Store  │  – sorted sets for latency percentiles
│                      │  – counters for traffic / errors
│                      │  – floats for saturation
│                      │  – configurable TTL per window
└────────┬─────────────┘
         │  ZRANGEBYSCORE / GET
         ▼
┌──────────────────────┐
│   Analytics API      │  :8001
│   FastAPI + uvicorn  │  – P50 / P95 / P99 percentiles
│                      │  – error rate, traffic, saturation
│                      │  – HITL/HOTL governance metadata
└──────────────────────┘
```

---

## Quick Start

```bash
# 1. Build all images
make build

# 2. Start the stack (detached)
make up

# 3. Seed 1,000 synthetic log entries
make seed

# 4. Run unit tests
make test

# 5. Run integration tests (requires running stack)
make test-integration

# 6. Tear down
make down
```

---

## API Reference

### Ingestion API `http://localhost:8000`

| Method | Path         | Description                                  |
| ------ | ------------ | -------------------------------------------- |
| GET    | `/health`    | Liveness check → `{"status":"ok"}`           |
| POST   | `/ingestion` | Ingest a batch of 1–1000 HAProxy log entries |

**POST /ingestion** request body:

```json
{
  "logs": [
    {
      "timestamp": "2026-05-31T10:00:00Z",
      "path": "/api/v1/users",
      "method": "GET",
      "status_code": 200,
      "response_time_ms": 45.3,
      "bytes_sent": 1024,
      "client_ip": "192.168.1.100",
      "backend_name": "backend1"
    }
  ]
}
```

Response: `{"accepted": 1, "rejected": 0, "errors": []}`

Headers: `X-Trace-Id` (generated or propagated)

---

### Analytics API `http://localhost:8001`

| Method | Path                | Description                        |
| ------ | ------------------- | ---------------------------------- |
| GET    | `/health`           | Liveness check                     |
| GET    | `/analytics/health` | Redis connectivity + tracked paths |
| GET    | `/analytics/paths`  | List all tracked paths             |
| GET    | `/analytics`        | Percentile analytics query         |

**GET /analytics** parameters:

| Parameter | Required | Values                                            | Default  |
| --------- | -------- | ------------------------------------------------- | -------- |
| `path`    | Yes      | any string                                        | —        |
| `signal`  | Yes      | `latency` \| `traffic` \| `error` \| `saturation` | —        |
| `window`  | Yes      | `1m` \| `5m`                                      | —        |
| `from`    | No       | ISO-8601 datetime                                 | now − 1h |
| `to`      | No       | ISO-8601 datetime                                 | now      |

Example:

```bash
curl "http://localhost:8001/analytics?path=/api/v1/users&signal=latency&window=1m&from=2026-05-31T10:00:00Z&to=2026-05-31T11:00:00Z"
```

---

### Metrics Processor `http://localhost:8002`

| Method | Path       | Description                                     |
| ------ | ---------- | ----------------------------------------------- |
| GET    | `/metrics` | `{"events_processed":N,"events_dlq":M,"lag":K}` |

---

## Configuration Reference

All configuration is via environment variables (set in `docker-compose.yml`).

| Variable                     | Service              | Default                 | Description                             |
| ---------------------------- | -------------------- | ----------------------- | --------------------------------------- |
| `REDIS_URL`                  | all                  | `redis://redis:6379/0`  | Redis connection URL                    |
| `SATURATION_BYTES_THRESHOLD` | ingestion, analytics | `1000000`               | bytes/min threshold for 100% saturation |
| `RETENTION_1M_SECONDS`       | processor            | `7200`                  | TTL for 1-minute window keys (2h)       |
| `RETENTION_5M_SECONDS`       | processor            | `86400`                 | TTL for 5-minute window keys (24h)      |
| `METRICS_PORT`               | processor            | `8002`                  | Port for /metrics HTTP endpoint         |
| `INGESTION_URL`              | seed script          | `http://localhost:8000` | Ingestion API base URL                  |
| `ANALYTICS_URL`              | integration tests    | `http://localhost:8001` | Analytics API base URL                  |

---

## Redis Key Patterns

```
gs:{signal}:{path}:{window}:{epoch_bucket}

Examples:
  gs:latency:/api/v1/users:1m:1748685600   → sorted set (response_time_ms values)
  gs:traffic:/api/v1/users:1m:1748685600   → integer counter
  gs:error:/api/v1/users:1m:1748685600     → integer counter (errors only)
  gs:error_total:/api/v1/users:1m:...      → integer counter (all requests)
  gs:saturation:/api/v1/users:1m:...       → float (bytes_sent sum)
  gs:paths                                 → Redis Set of all tracked paths
  golden-signals:events                    → Redis Stream (ingestion queue)
  golden-signals:dlq                       → Redis Stream (dead-letter queue)
  golden-signals:audit                     → Redis Stream (audit log)
```

---

## Agentic AI Copilot Integration

This infrastructure is designed as the data foundation for an **Agentic AI Copilot** that can:

1. **Query golden signals** via `GET /analytics` to detect SLO breaches in real time.
2. **Decide action mode** based on governance metadata in the response:
   - `recommended_action_mode: "HOTL"` — agent acts autonomously (p99 < 500ms, error_rate < 5%)
   - `recommended_action_mode: "HITL"` — human approval required (p99 ≥ 500ms or error_rate ≥ 5%)
3. **Reduce MTTD** by continuously monitoring all 5 Golden Signal dimensions.
4. **Reduce MTTR** by providing P50/P95/P99 latency trends to diagnose performance regressions.

The governance annotations are added in Wave 6 of the development roadmap.
