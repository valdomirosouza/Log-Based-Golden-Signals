# RB-SRE-GS-003 — log-shipper bridge: backpressure drops, syslog-edge down, env cold-start

**Scope:** the `gs-log-shipper` bridge and the `golden-signals` compose environment (SPEC-LGS-002,
ADR-0084 / ADR-0085). New operational failure modes introduced by the HAProxy→shipper→`/ingestion`
bridge and the health-ordered `golden-signals` compose profile. Demonstration rig — **not**
prod-deployed; Phase-13 deploy/rollback is N/A. Format follows `docs/sre/runbooks/README.md`
(blameless), as RB-SRE-GS-001/002.

> **PII (CLAUDE.md §3.1):** never paste a raw HAProxy line, a raw syslog frame, or a `client_ip`
> into an incident note — they carry telemetry-L2 PII (ENV-NFR-04). The shipper already refuses to
> log raw IPs; keep diagnostics to counts, `trace_id`, `batch_id`, status, and lifecycle events.

---

## Failure mode A — retry-exhaustion / backpressure drops

**Trigger:** `gs_shipper.entries_dropped_total` rising (today: `event=batch_dropped_retry_exhausted`
or `batch_dropped_unexpected_status` JSON log lines from `gs-log-shipper`); `entries_dropped_total`

> 0 violates the `parse_error_ratio`-adjacent loss budget and pulls `shipper_delivery_ratio` below
> the **99.9%** SLI (`docs/sre/slo/golden-signals-slo.yaml` `environment:` block).

**Detection (SLI/metric):** `shipper_delivery_ratio` = `entries_accepted_total / lines_parsed_total`;
`shipper_ship_lag_seconds` P99 climbing past **5s**. Source: shipper structured-JSON stdout (no
`/metrics` endpoint yet — see Known gap). `docker compose --profile golden-signals logs gs-log-shipper`
then aggregate `event` counts.

**Impact:** dropped entries are **counted, never silent** (ENV-FR-04) but still lost from Golden
Signals → traffic/error counts under-report during the burst window; G-03 (lossless bridge) degrades.

**Triage:**

1. Is `/ingestion` returning `429`/`503`? → the service is shedding load (rate limit / saturation),
   not a shipper bug. Confirm via `batch_retry` log events with `status=429|503`.
2. Is the retry budget exhausted? → `attempts` field on `batch_dropped_retry_exhausted` equals
   `GS_SHIPPER_MAX_RETRIES` (default 3). The drop is the **designed** terminal state, not a crash.
3. Is the burst the `latency-burst`/`error-burst` generator scenario (ENV-FR-07)? → expected; this
   is the AC-07 path exercising backpressure, self-corrects when the burst ends.

**Mitigation:**

- Transient burst: bounded retry + drop-and-count is by design (ADR-0084 §4); wait for drain — the
  generator scenario ends and `entries_dropped_total` stops rising.
- Sustained: raise `GS_SHIPPER_MAX_RETRIES` / `GS_SHIPPER_BACKOFF_MAX_SECONDS`, or reduce generator
  rate (`GS_DEMO_TOTAL_REQUESTS`). If `/ingestion` itself is the bottleneck, see RB-SRE-GS-002
  (worker backlog) / RB-SRE-GS-001 (store).
- Never "fix" a drop by removing the counter or the retry cap — that converts a counted loss into a
  silent one (ENV-FR-04 violation).

---

## Failure mode B — syslog-edge down (shipper listener not bound)

**Trigger:** `gs-haproxy` stuck `unhealthy` / not starting; HAProxy log target `gs-log-shipper:514`
unreachable. The compose graph gates HAProxy on the shipper being **healthy** (listener bound), so a
shipper that never binds blocks HAProxy startup (ENV-FR-06). Absent `event=syslog_listener_started`
in shipper logs.

**Detection (SLI/metric):** `env_cold_start_seconds` exceeds **120s** (never reaches all-healthy);
`docker compose --profile golden-signals ps` shows `gs-log-shipper` not `healthy`. Healthcheck is a
TCP connect to `GS_SHIPPER_SYSLOG_PORT` (514) — a failing check means the listener isn't bound.

**Impact:** no HAProxy lines reach the shipper → zero traffic into `/ingestion` → Golden Signals
empty. This is the Defect-B failure class (issue #28): the original stdin design EOF-looped because
the topology never connected HAProxy stdout to shipper stdin; the TCP syslog edge (ADR-0084
Amendment) replaced it. TCP (not UDP) is deliberate — UDP would silently drop the first hop.

**Triage:**

1. `gs-log-shipper` logs → is `syslog_listener_started host=... port=514 transport=tcp` present?
2. Did the shipper fail config at start? → a missing `GS_API_KEYS` raises a fail-fast `ValueError`
   (ENV-FR-09) before the listener binds. Check for an early exit, not a hang.
3. Is `golden-signals` healthy first? The shipper depends on it (`service_healthy`); a stuck service
   keeps the shipper from starting (cascade — check RB-SRE-GS-001).

**Mitigation:**

- Missing key: set `GS_API_KEYS` in `.env` (never commit it) and `make gs-down && make gs-up`.
- Listener not binding: confirm `GS_SHIPPER_SYSLOG_PORT` matches HAProxy's `log tcp@gs-log-shipper:514`
  and both sit on `gs-net`; restart the profile.
- Upstream dependency stuck: resolve `golden-signals`/`redis` health first (RB-SRE-GS-001), the
  shipper then starts automatically.

---

## Failure mode C — environment cold-start exceeds budget

**Trigger:** `make gs-up` does not reach all-healthy within the **`env_cold_start_seconds` ≤ 120s**
SLI; one or more of `redis` / `golden-signals` / `gs-log-shipper` / `gs-haproxy` stuck `starting`.

**Detection (SLI/metric):** `env_cold_start_seconds`; `docker compose --profile golden-signals ps`
column `STATUS` not all `healthy`. Health-ordered chain: `redis` healthy → `golden-signals` healthy
(`GET /analytics/health` 200) → `gs-log-shipper` (listener bound) → `gs-haproxy` (ENV-FR-06).

**Impact:** the demonstration rig is not runnable; `gs-demo`/`gs-smoke` and the SPEC-LGS-001
acceptance evidence (AC-01/AC-10) cannot be produced.

**Triage:**

1. Which container is the chain stalled on? Inspect that one's healthcheck first (the chain is
   strictly ordered — the first non-healthy node blocks all downstream).
2. First-run image build cost (shipper / stub / generator built locally) can dominate cold-start on a
   cold cache — distinguish "build slow" from "container unhealthy".
3. Resource limits: each container has CPU/mem limits (ENV-FR-11); on a constrained runner a tight
   limit can slow startup. Confirm the aggregate ceiling fits the runner (ENV-NFR-07).

**Mitigation:**

- Slow first build: pre-build images (`make gs-up` again after the cache is warm) before timing
  cold-start.
- Stalled node: drop to the relevant runbook — `redis`/`golden-signals` → RB-SRE-GS-001; shipper
  listener → Failure mode B above.
- Persistent: `make gs-down` (surgical teardown, no orphan volumes — ENV-FR-10) then `make gs-up`.

---

## Known gap (Phase-11, honest record — CLAUDE.md §3.6)

- **No scrapeable `/metrics` on the shipper.** `ShipperCounters` are in-process and surfaced **only**
  as structured-JSON stdout events. The four `environment:` SLIs are therefore derived from **log
  aggregation**, not a Prometheus scrape. Wiring a `/metrics` endpoint (a small HTTP server thread +
  a host/compose port) is a documented follow-up, not yet implemented (would add a listening port →
  not a low-risk inline change for this phase). Until then, detection above relies on log events.
- **Trace correlation is partial.** The shipper sets `X-Trace-Id` per batch on every `POST /ingestion`
  (verified; ENV-NFR-03/AC-12), so the **shipper→service** hop is correlatable. But HAProxy does not
  yet emit a `unique-id`/trace token in the access line, and the `golden-signals` **stub** does not
  record `X-Trace-Id` in an audit entry — so the full **HAProxy→shipper→service-audit** chain (AC-12)
  closes only when the real Java service (DEFERRED) records it. The shipper-generated trace id bounds
  the hop it owns.

---

## Escalate

- `entries_dropped_total` non-zero and rising > 15 min under steady (non-burst) traffic, **or**
  `shipper_delivery_ratio` < 99.9% sustained, **or** `env_cold_start_seconds` > 120s after a warm
  build → page **SRE Lead** (RTO `dora_mttr_target_seconds: 3600`, `docs/sre/slo/slo.yaml`).
- This is a demonstration rig: there is **no production deploy/rollback** path (Phase-13 N/A). "Recovery"
  is `make gs-down && make gs-up`, not a release rollback.

**Links:** ADR-0084 (haproxy-log-shipping-bridge + Amendment 2026-06-17), ADR-0085
(golden-signals-compose-environment), SPEC-LGS-002 §10(b) (env SLIs), `docs/sre/slo/golden-signals-slo.yaml`
(`environment:` block), RB-SRE-GS-001 (store unavailable), RB-SRE-GS-002 (freshness lag). Issues #18,
#28.
