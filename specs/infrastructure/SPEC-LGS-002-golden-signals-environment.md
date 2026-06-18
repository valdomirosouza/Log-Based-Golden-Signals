---
# ─────────────────────────────────────────────────────────────────────────
# SPEC METADATA  (machine-readable header — /deliver and CI read this block)
# Place at: specs/infrastructure/SPEC-LGS-002-golden-signals-environment.md
# Reuse: copy specs/SPEC-TEMPLATE.md; this file keeps the section skeleton that
# maps 1:1 onto the 15-phase Agentic Spec-Driven Delivery workflow (ADR-0058).
# ─────────────────────────────────────────────────────────────────────────
id: SPEC-LGS-002
title: Log-Based Golden Signals — Containerised Runtime Environment & HAProxy Log-Shipping Bridge
version: 0.2.0 # Phase-4 Specification: moved to canonical path; K=1 deviation fixed (AC-15);
# six §15 open questions resolved; four §10 SLO objectives proposed; lightweight DPIA note recorded.
status: approved # draft | in-review | approved | implemented | superseded
# APPROVED 2026-06-16 by valdomirosouza (Tech Lead + acting Security Lead dual sign-off), Refs #18.
# Q3/Q4/Q5 recommended dispositions + four §10 SLO objectives confirmed at the Phase-4 gate.
owner: valdomirosouza # Tech Lead / SRE Lead
created: 2026-06-16
updated: 2026-06-16 # Phase 4 — Specification (see §17 Spec Changelog)
source: >-
  Academic article "Log-Based Golden Signals: A Scalable Ingestion and Predictive
  Analytics Infrastructure for Agentic AI Copilots" (V. de O. Souza Jr.,
  PPGCA/Unisinos, 2026), §3.4 (Ferramentas, Linguagens e Bibliotecas) — the
  Docker/Docker-Compose environment that stands up the pipeline. Companion to the
  application logic already specified in SPEC-LGS-001.
deployment_topology: monorepo-services # the environment lives in this monorepo (compose + infrastructure/) — §1.4
governing_adrs: [
    ADR-0003, # async / event-decoupling strategy
    ADR-0011, # HITL/HOTL model
    ADR-0012, # PII masking (client IP)
    ADR-0020, # FinOps / cost envelope
    ADR-0026, # audit-log immutability
    ADR-0029, # DevSecOps pipeline security (image scan, SBOM)
    ADR-0066, # runtime stack: Java 21 / Spring Boot (the service image this env runs)
    ADR-0067, # Redis as time-series store (the container this env provisions)
    ADR-0068, # Golden-Signal extraction rules (the log fields HAProxy must emit)
    ADR-0069, # in-JVM bounded virtual-thread queue (intra-service; env exposes nothing)
  ]
new_adrs_required: [
    # PROPOSED numbers (next free after ADR-0083; authored at Phase 5, NOT here — CLAUDE.md §3.6 grounding).
    haproxy-log-shipping-bridge, # PROPOSED ADR-0084 — HAProxy syslog/access-logs -> POST /ingestion JSON batches
    golden-signals-compose-environment, # PROPOSED ADR-0085 — `golden-signals` compose profile, gs-net isolation, depends_on graph
    demonstration-traffic-generator, # PROPOSED ADR-0086 — deterministic synthetic-traffic injector used to exercise ACs
  ]
related_specs:
  [
    specs/system/SPEC-LGS-001-log-based-golden-signals.md,
    specs/features/SPEC-LGS-001-golden-signals-feature-spec.md,
    specs/security/threat-model-SPEC-LGS-001-golden-signals.md,
    specs/k8s/probe-strategy.md,
    specs/privacy/,
  ]
slo_ref: docs/sre/slo/golden-signals-slo.yaml
---

# SPEC-LGS-002 — Log-Based Golden Signals: Containerised Runtime Environment

> **One-line scope.** A reproducible, governed **Docker Compose environment** that runs a real
> **HAProxy** log source, a **log-shipping bridge** that turns HAProxy access logs into
> `POST /ingestion` JSON batches, the `golden-signals` service, and **Redis** — on an isolated
> network with health-ordered startup, an env-driven config surface, a deterministic traffic
> generator, and a single `make` target that brings the pipeline up and demonstrates it end-to-end.

<!-- HOW TO USE THIS TEMPLATE
  • Every numbered section is mandatory. "N/A — <reason>" where it genuinely does not apply.
  • (gate) sections are checked by a phase gate in docs/process/gates/phase-gates.yaml.
  • Write code only after this spec reaches status: approved (CLAUDE.md §2; no code without a spec).
  • `/deliver specs/infrastructure/SPEC-LGS-002-golden-signals-environment.md` drives it through all
    15 phases as a governed dry-run and emits reports/SPEC-LGS-002/FINAL-REPORT.md.
  • SCOPE BOUNDARY: this spec owns the ENVIRONMENT (orchestration, log source, log-shipping, the
    demonstrable end-to-end run). It does NOT re-specify the application's internal logic — that is
    owned by SPEC-LGS-001 (system) and its feature spec. Where the two meet, this spec references
    SPEC-LGS-001 by FR/AC id rather than restating it.
-->

## How `/deliver` reads this spec (section → phase)

| Spec section                                         | Feeds /deliver phase(s)                  | Gate it satisfies                                |
| ---------------------------------------------------- | ---------------------------------------- | ------------------------------------------------ |
| §1 Context, §2 Goals, §3 Non-Goals, §4 Consumers     | 0 Intake · 1 Conception                  | problem/value/risk recorded                      |
| §5 FR, §6 NFR                                        | 2 Discovery · 4 Specification            | discovery + nfr; FR→AC traceability              |
| §6 NFR (PII rows), §11 Governance/Privacy            | 2 Discovery · 9 Security & DevSecOps     | PII classification; threat & privacy review      |
| §7 Architecture, §14 ADR Impact, `new_adrs_required` | 5 Architecture                           | ADR(s) authored & accepted                       |
| §8 Interface Contracts (gate)                        | 4 Specification · 6 Development          | contract-driven dev (compose + shipper config)   |
| §9 Data Model                                        | 6 Development · 9 Security               | log-line → schema mapping; key/injection safety  |
| §10 Golden Signals & SLO (gate)                      | 11 Observability & Operational Readiness | SLOs + PRR                                       |
| §11 Governance/Privacy/Security (gate)               | 9 DevSecOps · 10 AI Safety (n/a here)    | STRIDE; AI-safety conditional (no agent built)   |
| §12 Acceptance Criteria (gate)                       | 8 Testing · all phases                   | **becomes the dry-run evidence in FINAL-REPORT** |
| §13 Risks, §15 Open Questions                        | every phase boundary                     | surfaced as HITL items                           |

---

## 1. Context & Problem

### 1.1 Problem statement

`SPEC-LGS-001` specifies the four-component Golden-Signals pipeline (Ingestion API → internal queue
→ Metrics Processor → Redis store → Analytics API) and its Java/Spring Boot implementation. But a
**specified application is not a running pipeline.** The article (§3.4) names the environment —
HAProxy, Redis, Docker, Docker Compose — yet two things remain unspecified and block any real
end-to-end run:

1. **There is no defined bridge from HAProxy to the Ingestion API.** HAProxy emits **syslog /
   access-log lines**; the Ingestion API consumes **JSON batches over `POST /ingestion`** (SPEC-LGS-001
   §8). The article's diagram draws an arrow between them but no component performs the parse →
   normalise → batch → HTTP-POST translation. Without it, the pipeline has no real input.
2. **There is no reproducible, governed orchestration** that stands up the log source, the bridge,
   the service and the store together, in the right order, on an isolated network, with the
   demonstration data needed to prove the SPEC-LGS-001 acceptance criteria (AC-01/04/05/08/10) at the
   environment level.

The cost of not solving it: SPEC-LGS-001 stays a paper design; the master's-research demonstration
("a data foundation an Agentic Copilot consumes to reduce MTTD/MTTR") cannot be exercised, measured,
or shown; and every reviewer must hand-assemble a bespoke, non-reproducible test rig.

### 1.2 Research / product question

How can the SPEC-LGS-001 pipeline be stood up as a **single-command, reproducible, governed
environment** — fed by a **real HAProxy log source through an explicit log-shipping bridge** — so the
four Golden Signals are demonstrably computed from genuine proxy traffic, and the SPEC-LGS-001
acceptance criteria become runnable evidence rather than assertions?

### 1.3 Why now / motivation

SPEC-LGS-001 has reached an implementable feature spec; the `golden-signals` service is already
registered in `services.yaml` (Java, port 8085) and Redis already exists in `docker-compose.yml`. The
missing piece for the dissertation demonstration and for any `/deliver code` run of SPEC-LGS-001 is
the **runtime environment and its log feed**. Specifying it now unblocks the demonstrable MTTD/MTTR
narrative and gives the future Agentic-Copilot layer a real endpoint to read.

### 1.4 Deployment topology decision _(decide before Phase 1)_

**Chosen: `monorepo-services`.** The environment is expressed as a new Docker Compose **profile**
(`golden-signals`) plus supporting config under `infrastructure/golden-signals/` (HAProxy config,
log-shipper config, traffic-generator), reusing the repo's existing `redis` service, network model,
healthcheck conventions, `.env.example`, CI image-scan/SBOM gates and the 15-phase governance. The
service image itself is built per **ADR-0066 (Java 21 / Spring Boot)** — this environment treats
`golden-signals` as a **black-box container** honouring the SPEC-LGS-001 §8 HTTP contract, so the
orchestration is **language-neutral** and survives the Python-vs-Java question recorded in §15.

A `standalone-repo` (a throwaway compose project) is rejected: it would duplicate Redis, lose the
shared CI/governance, and drift from the registered `golden-signals` service.

---

## 2. Goals & Success Metrics

| ID   | Goal                                  | Measure of success                                                                                                                                                 |
| ---- | ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| G-01 | One-command, reproducible environment | `make gs-up` (compose profile `golden-signals`) brings HAProxy + log-shipper + `golden-signals` + Redis to **healthy** from a clean checkout, no manual steps      |
| G-02 | Real log source, not synthetic POSTs  | The four Golden Signals are computed from **HAProxy access-log lines** routed through the shipping bridge — not from test fixtures POSTed directly to `/ingestion` |
| G-03 | Explicit, lossless shipping bridge    | 100% of HAProxy-logged requests for monitored paths appear in `GET /analytics` traffic counts (±0 under steady state; documented loss budget under backpressure)   |
| G-04 | Demonstrable acceptance evidence      | A single `make gs-demo` produces a transcript that satisfies SPEC-LGS-001 AC-01/04/05/08/10 against the running environment                                        |
| G-05 | Governed & bounded footprint          | Every container has CPU/memory limits (ADR-0020); no secrets in the tree; surgical teardown leaves no orphan volumes                                               |
| G-06 | Privacy preserved end-to-end          | No unmasked client IP is observable anywhere in the environment (HAProxy logs, shipper, network, store, container logs)                                            |

---

## 3. Non-Goals / Out of Scope

- **The application's internal logic.** Percentile maths, masking implementation, windowing, the
  `_governance` block, queue/worker design — owned by SPEC-LGS-001 + its feature spec. This spec
  only _runs_ that service and _feeds_ it.
- **The Agentic AI Copilot / any LLM inference.** SPEC-LGS-001 §3 already scopes it out; this
  environment merely exposes the `/analytics` endpoint it will later consume.
- **Production cloud infrastructure.** AWS/EKS/managed Redis is owned by `SPEC-INFRA-001`. This is a
  **local / CI demonstration environment** (Docker Compose), not a production deployment.
- **HA / clustered Redis, multi-tenant scaling.** Single-node Redis by design (SPEC-LGS-001 §3, §13).
- **Distributed-tracing ingestion.** Logs → Golden Signals only.
- **Choosing the service's programming language.** Bound by ADR-0066 (Java); this spec is neutral to
  it and only depends on the §8 HTTP contract.

---

## 4. Consumers & Personas

| Consumer                                     | Need from this environment                                                                                                     |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| Researcher / dissertation reviewer (primary) | A reproducible rig that demonstrates Golden Signals computed from real HAProxy traffic, on one command                         |
| `/deliver` (CODE mode, SPEC-LGS-001)         | A runnable target to validate the service's acceptance criteria against (AC-01/04/05/08/10)                                    |
| SRE / NOC engineer                           | A local replica of the ingestion path to reproduce incident-time `/analytics` behaviour and tune HITL thresholds               |
| Future Agentic Copilot layer                 | A live `GET /analytics` endpoint exposing governed percentiles to reason over                                                  |
| Platform / governance owner                  | Evidence that PII masking, network isolation, image scanning, cost limits and audit controls hold in the assembled environment |

---

## 5. Functional Requirements

<!-- EARS-style. Each FR traces to an AC in §12. These are ENVIRONMENT requirements; application
     behaviour lives in SPEC-LGS-001 and is referenced, not restated. ENV-FR-* ids avoid collision
     with SPEC-LGS-001 FR-* ids. -->

| ID        | Requirement (EARS: WHEN … the system SHALL …)                                                                                                                                                                                                                                                                                                                                                                                             |
| --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ENV-FR-01 | WHEN `docker compose --profile golden-signals up` is run, the environment SHALL start `haproxy`, `gs-log-shipper`, `golden-signals` and `redis` on a single isolated bridge network and reach an all-healthy state with no manual intervention.                                                                                                                                                                                           |
| ENV-FR-02 | The `haproxy` container SHALL be configured with a **pinned access-log format** that emits every field the four Golden Signals require per request: request path, method, HTTP status, **server response time `%Tr` (ms) as the Golden-Signals latency field** (with total time `%Tt` additionally captured for context — §15-Q4, PROPOSED ADR-0084), bytes sent, client IP, backend name, and a timestamp (ADR-0068; SPEC-LGS-001 §9.1). |
| ENV-FR-03 | WHEN HAProxy logs a request, the `gs-log-shipper` SHALL parse the line, normalise it to the canonical inbound log-entry schema (epoch-millisecond timestamp, see §9), batch entries, and deliver them to `golden-signals` via `POST /ingestion` authenticated with `GS_API_KEYS`.                                                                                                                                                         |
| ENV-FR-04 | WHEN `POST /ingestion` returns `429` or `503`, the `gs-log-shipper` SHALL apply bounded retry with backoff (honouring `Retry-After`) and SHALL count, but never silently discard without recording, any entries dropped after the retry budget is exhausted.                                                                                                                                                                              |
| ENV-FR-05 | The environment SHALL provision a single-node `redis` (reuse of the repo's `redis` service) reachable only on the internal network, password-protected via `REDIS_PASSWORD`, with the retention-relevant env vars (`RETENTION_1M_SECONDS`, `RETENTION_5M_SECONDS`) wired into `golden-signals` (ADR-0067).                                                                                                                                |
| ENV-FR-06 | The compose graph SHALL enforce health-ordered startup via `depends_on … condition: service_healthy`: `redis` healthy → `golden-signals` healthy (`GET /analytics/health` 200) → `gs-log-shipper` starts → `haproxy` starts.                                                                                                                                                                                                              |
| ENV-FR-07 | The `gs-traffic-generator` SHALL produce **deterministic** synthetic HTTP load against HAProxy across **≥ 5 distinct paths**, with a selectable scenario that injects a high-latency / elevated-error burst sufficient to trip the SPEC-LGS-001 FR-13 HITL thresholds.                                                                                                                                                                    |
| ENV-FR-08 | The repository SHALL expose `make gs-up`, `make gs-demo`, `make gs-smoke`, and `make gs-down` targets that respectively start the environment, run the end-to-end demonstration, run a fast health-only check, and tear the environment down surgically.                                                                                                                                                                                  |
| ENV-FR-09 | All environment configuration SHALL be supplied via environment variables with documented defaults in `.env.example`; the environment SHALL start from defaults alone except for the values marked _(required)_ (e.g. `GS_API_KEYS`), and SHALL contain **no committed secrets**.                                                                                                                                                         |
| ENV-FR-10 | WHEN `make gs-down` is run, the environment SHALL stop all profile containers and remove their named volumes, leaving no orphaned containers, networks, or volumes (DRY-RUN of teardown must show zero residue).                                                                                                                                                                                                                          |
| ENV-FR-11 | Every container in the profile SHALL declare explicit CPU and memory limits/reservations recorded against the cost envelope (ADR-0020).                                                                                                                                                                                                                                                                                                   |
| ENV-FR-12 | The environment SHALL keep all inter-service traffic (HAProxy→shipper→service→Redis) on the internal bridge network, exposing to the host **only** the HAProxy listener and the `golden-signals` API port (8085); Redis SHALL NOT be host-published in the default profile.                                                                                                                                                               |

---

## 6. Non-Functional Requirements

<!-- Classify each by docs/product/nfr-taxonomy.md (category + evidence/gate). -->

| ID         | Requirement                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ENV-NFR-01 | **Containerisation / reproducibility.** All components are containers orchestrated by Docker Compose under the `golden-signals` profile; pinned base-image tags (no `latest`); a clean checkout reproduces the environment bit-for-bit. _(Category: Operability; evidence: compose file + AC-01.)_                                                                                                                                                                    |
| ENV-NFR-02 | **Config-via-env.** 100% of tunables via env vars with defaults in `.env.example`; no hard-coded hosts/ports/keys in images. _(Category: Operability; evidence: `.env.example` diff, AC-11.)_                                                                                                                                                                                                                                                                         |
| ENV-NFR-03 | **Structured logging + trace propagation.** Shipper and generator emit structured JSON logs; the shipper SHALL set/propagate `X-Trace-Id` on each `POST /ingestion` so a request is traceable HAProxy→shipper→service (NFR-03 of SPEC-LGS-001). _(Category: Observability; evidence: log sample, AC-12.)_                                                                                                                                                             |
| ENV-NFR-04 | **PII safety in transit.** Client IP is masked **before persistence/logging** by the service (SPEC-LGS-001 FR-02); additionally the environment SHALL NOT expose raw HAProxy logs outside the internal network, and the shipper SHALL NOT write raw client IP to its own stdout. Classify the shipped log-entry payload as **telemetry-L2 with a PII field (`client_ip`)**. _(Category: Privacy/PII; evidence: AC-06 + PiiLeak scan; maps ADR-0012, specs/privacy/.)_ |
| ENV-NFR-05 | **Image supply-chain.** Every image built or pulled is scanned (Trivy) and an SBOM produced in CI; no `CRITICAL` unfixed vuln ships (ADR-0029). _(Category: Security/DevSecOps; evidence: CI scan log + SBOM artifact.)_                                                                                                                                                                                                                                              |
| ENV-NFR-06 | **Resilience under load.** The shipper handles ingestion backpressure (ENV-FR-04) without crashing; the environment survives a Redis restart with bounded, documented data loss (in-flight aggregates only). _(Category: Reliability; evidence: AC-08/AC-10.)_                                                                                                                                                                                                        |
| ENV-NFR-07 | **Cost envelope.** Aggregate CPU/memory ceiling for the whole profile documented and enforced (ENV-FR-11); fits a developer laptop / CI runner. _(Category: Cost; maps ADR-0020.)_                                                                                                                                                                                                                                                                                    |
| ENV-NFR-08 | **Pinned dependencies + manifest.** Shipper/generator dependencies pinned (no ranges); `dependency-manifest.yaml` + SBOM produced (documentation-standards). _(Category: Supply-chain; evidence: manifest + lockfile.)_                                                                                                                                                                                                                                               |

> Classify each NFR by `docs/product/nfr-taxonomy.md` — categories assigned inline above; the
> binding evidence/gate is named per row.

---

## 7. Architecture

Three functional layers, all on one isolated Docker network. **New** components introduced by this
spec are marked **[NEW]**; existing ones are reused as-is.

```
                         ┌───────────────────────── compose profile: golden-signals ─────────────────────────┐
                         │  network: gs-net (internal bridge; only :HAPROXY and :8085 host-published)         │
                         │                                                                                    │
 gs-traffic-generator    │   ┌──────────────┐  access-log line   ┌──────────────────┐  POST /ingestion        │
 [NEW] deterministic ───────▶│   haproxy    │ ─(syslog/stdout)─▶ │  gs-log-shipper  │ ─(JSON batch + key + ─┐ │
 synthetic HTTP load     │   │ v2.8+ LTS    │  pinned log-format │  [NEW] parse →    │   X-Trace-Id)         │ │
 (≥5 paths, latency/     │   │ (LOG SOURCE) │  (ADR-0068 fields) │  normalise →      │                       │ │
 error scenarios)        │   └──────────────┘                    │  batch → ship,    │                       │ │
                         │          ▲ proxies a tiny upstream     │  retry/backoff    │                       │ │
                         │          │ (or returns canned          │  (ENV-FR-04)      │                       │ │
                         │          │  responses) so it logs      └──────────────────┘                       │ │
                         │          │  realistic traffic                                                      ▼ │
                         │          │                                              ┌───────────────────────────┐│
                         │          └──────────────────────────────────────────── │  golden-signals (8085)    ││
                         │                                                         │  Java 21 / Spring Boot    ││
                         │                                                         │  (ADR-0066) — BLACK BOX,  ││
                         │                                                         │  honours SPEC-LGS-001 §8  ││
                         │                                                         └─────────────┬─────────────┘│
                         │   GET /analytics ◀── (Agentic layer / reviewer / make gs-demo)        │ persist      │
                         │                                                          ┌────────────▼────────────┐ │
                         │                                                          │  redis v7 (reused)      │ │
                         │                                                          │  single-node, internal, │ │
                         │                                                          │  TTL retention ADR-0067 │ │
                         │                                                          └─────────────────────────┘ │
                         └────────────────────────────────────────────────────────────────────────────────────┘
```

**The central new component — `gs-log-shipper`** — is the bridge the article's diagram implies but
does not specify. Its contract (input log format → output JSON schema) is pinned in §8/§9. Its
internal implementation (off-the-shelf Vector/Fluent Bit with a transform, or a small purpose-built
forwarder) is an **architecture-phase decision** recorded in the `haproxy-log-shipping-bridge` ADR;
align its delivery semantics with **ADR-0003** (decoupling, at-least-once with idempotent counting).

**HAProxy** is configured as a real proxy in front of a trivial upstream (or returning canned
responses) purely so it produces genuine access logs across the generated paths; it is the **log
source**, not a component under test.

The `golden-signals` service and `redis` are reused exactly as registered/declared; this spec adds
**no** intra-service architecture (queue, worker, percentile maths stay in SPEC-LGS-001 / ADR-0069).

---

## 8. Interface Contracts _(gate: contract-driven dev)_

This environment's "contracts" are the **compose service contract**, the **HAProxy log-line
contract**, and the **shipper→service HTTP contract** (the last one is reused verbatim from
SPEC-LGS-001 §8 — the shipper is just another client of `/ingestion`).

| Surface                 | Producer → Consumer                    | Contract                                                                                                                                                              | Success                                        | Errors                                                                                                     |
| ----------------------- | -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| HAProxy access log      | `haproxy` → `gs-log-shipper`           | Pinned `log-format` string emitting the ADR-0068 fields (path, method, status, `%Tr`/response-time-ms with `%Tt` for context — §15-Q4, `B`/bytes, client IP, backend) | Line parses to all required fields             | Unparseable line → shipper drops + increments `gs_shipper_parse_errors_total`, never crashes               |
| `POST /ingestion`       | `gs-log-shipper` → `golden-signals`    | SPEC-LGS-001 §8: JSON array of log entries (§9.1), `GS_API_KEYS` header, `X-Trace-Id`                                                                                 | `202 {accepted, rejected}`                     | `401` (bad key) → fail-fast + alert; `422` (bad batch) → log + drop batch; `429`/`503` → retry (ENV-FR-04) |
| `GET /analytics/health` | `golden-signals` → compose healthcheck | SPEC-LGS-001 §8                                                                                                                                                       | `200 {status, redis_connected, tracked_paths}` | `503` → container marked unhealthy → startup gate holds                                                    |
| Generator HTTP          | `gs-traffic-generator` → `haproxy`     | Drives ≥5 paths; scenario flag `GS_DEMO_SCENARIO ∈ {steady, latency-burst, error-burst}`                                                                              | HAProxy logs each request                      | n/a (fire-and-measure)                                                                                     |
| `make` targets          | operator → environment                 | `gs-up` / `gs-demo` / `gs-smoke` / `gs-down` (ENV-FR-08)                                                                                                              | exit `0` + transcript                          | non-zero exit with diagnostic                                                                              |

The compose service definitions (image tags, ports, env, healthcheck, depends_on, deploy limits)
are generated **from this section** into `docker-compose.yml` (profile `golden-signals`) and
`infrastructure/golden-signals/`. Do not hand-drift them from the spec.

---

## 9. Data Model

### 9.1 Entities / payloads (validated at boundaries)

**HAProxy access-log line → canonical log entry.** The shipper maps HAProxy capture fields to the
SPEC-LGS-001 §9.1 schema. The canonical entry (the validated boundary object POSTed to `/ingestion`):

| Canonical field    | Type               | Source in HAProxy log        | Notes                                                                                                                                                                |
| ------------------ | ------------------ | ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `timestamp`        | int (epoch **ms**) | accept-date / request ts     | **Epoch-millis, not ISO-8601** (SPEC-LGS-001 §9.1 clarification). Shipper converts.                                                                                  |
| `path`             | string             | captured request path        | URL-encoded before any key assembly downstream (ADR-0068) — service responsibility, but shipper must not corrupt it                                                  |
| `method`           | string             | request method               |                                                                                                                                                                      |
| `status_code`      | int                | `%ST`                        | `>= 400` ⇒ error signal (service-side)                                                                                                                               |
| `response_time_ms` | float              | `%Tr` (server response time) | latency sample — **`%Tr`** chosen over `%Tt` so client-side time does not inflate server latency (§15-Q4, PROPOSED ADR-0084); `%Tt` also captured as a context field |
| `bytes_sent`       | int                | `%B`                         | saturation proxy vs `SATURATION_BYTES_THRESHOLD` (service-side)                                                                                                      |
| `client_ip`        | string (**PII**)   | `%ci`                        | **masked by the service before persist/log** (FR-02). Shipper transmits over internal net only; does not log it raw (ENV-NFR-04)                                     |
| `backend_name`     | string (optional)  | `%b`                         |                                                                                                                                                                      |

### 9.2 Storage key/schema convention

**N/A — owned by SPEC-LGS-001 §9.2 + ADR-0068** (`gs:{signal}:{path}:{window}:{epoch_bucket}` and the
latency sorted-set). This environment provisions the Redis that holds those keys but defines none of
them. Recorded here only to make the boundary explicit.

### 9.3 Retention

Retention is enforced **by the service** via TTL (ADR-0067), driven by env vars this environment
wires (`RETENTION_1M_SECONDS=7200`, `RETENTION_5M_SECONDS=86400`). The environment adds a Redis
persistence policy choice (`--save` vs ephemeral) recorded in the compose ADR; default for a
demonstration environment: **ephemeral** (no `--save`), so `make gs-down` leaves no historical data.

### 9.4 Governance/response metadata

**N/A — the `_governance` block is produced by the service** (SPEC-LGS-001 §9.4 / FR-12). The
environment's job is only to make a scenario occur (ENV-FR-07) such that the block flips to HITL,
and to capture it as demonstration evidence (AC-09).

---

## 10. Golden Signals & SLO Definitions _(gate: observability)_

This environment is observed at **two levels** and must not conflate them.

**(a) The Golden Signals the pipeline COMPUTES about HAProxy traffic** — defined in SPEC-LGS-001 §10
/ ADR-0068; reproduced here only as the _demonstration target_:

| Signal     | Derivation                                                           | Exposed as                            |
| ---------- | -------------------------------------------------------------------- | ------------------------------------- |
| Traffic    | request count per `(path, window)` from shipped HAProxy lines        | count per bucket via `GET /analytics` |
| Latency    | `response_time_ms` (`%Tr`, server response time — §15-Q4) per window | P50 / P95 / P99 per bucket            |
| Error      | `status_code >= 400`                                                 | error_rate = errors / total           |
| Saturation | `bytes_sent` (`%B`) vs `SATURATION_BYTES_THRESHOLD`                  | saturation_pct                        |

**(b) SLIs of the ENVIRONMENT itself** (new, owned here) — to be added to
`docs/sre/slo/golden-signals-slo.yaml` under an `environment:` block, distinct from the existing
service SLOs:

| Env SLI                    | Definition                                                                   | Objective (Phase-4 proposal)                                          |
| -------------------------- | ---------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `shipper_delivery_ratio`   | entries delivered to `/ingestion` ÷ HAProxy lines logged for monitored paths | **≥ 99.9%** steady-state — `RECOMMENDED` (pending confirmation)       |
| `shipper_ship_lag_seconds` | time from HAProxy log emit to `202` from `/ingestion`                        | **P99 ≤ 5s** — `RECOMMENDED` (pending confirmation)                   |
| `env_cold_start_seconds`   | `gs-up` invocation to all-healthy                                            | **≤ 120s** on reference runner — `RECOMMENDED` (pending confirmation) |
| `parse_error_ratio`        | unparseable lines ÷ total                                                    | **≤ 0.1%** — `RECOMMENDED` (pending confirmation)                     |

> **`RECOMMENDED` (pending Phase-4 human confirmation).** The four objectives above are now set to
> concrete proposed values (previously unset confirmation placeholders). They are SLO _proposals_ for the
> tech_lead + security_lead to confirm at the approval gate, after which they are written into the
> `environment:` block of `docs/sre/slo/golden-signals-slo.yaml` (a Phase-6/11 action, not this phase).

Thresholds that flip a _response_ to HITL are unchanged — they live in the service and in
`golden-signals-slo.yaml` `hitl_triggers` (p99 1000ms, error_rate 0.05). ENV-FR-07's `latency-burst`
/ `error-burst` scenarios exist precisely to drive traffic past those thresholds for AC-09.

---

## 11. Governance, Privacy & Security _(gate: threat & privacy review)_

| Concern                                  | Control in this spec                                                                                                                                                                                                                                                                                                                                                                               | Maps to                                                           |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| Human oversight (HITL/HOTL)              | Environment is a closed demonstration rig; it performs **no autonomous outward action**. It _surfaces_ the service's HITL flip (ENV-FR-07/AC-09) but never acts on it. `/deliver` itself stops at every human gate.                                                                                                                                                                                | ADR-0011                                                          |
| PII (classify L1–L4; mask at boundaries) | `client_ip` classified **telemetry-L2 / PII**; masked by service before persist/log (FR-02); raw logs confined to internal network; shipper forbidden from logging raw IP (ENV-NFR-04). **DPIA disposition (Phase 2):** lightweight register note — synthetic + masked data; full DPIA **waived** per human disposition. Recorded as **Activity 6** in `docs/privacy/data-processing-register.md`. | ADR-0012, `docs/privacy/data-processing-register.md` (Activity 6) |
| Auditability (immutable trail)           | Service audit trail (FR-14) is unchanged; the environment ensures the shipper sets `X-Trace-Id` so HAProxy→shipper→service→audit is correlatable.                                                                                                                                                                                                                                                  | ADR-0026                                                          |
| Authn / abuse (auth, rate limit)         | Shipper authenticates with `GS_API_KEYS`; key supplied via env, never committed; ingestion rate-limit (FR-11) exercised by ENV-FR-07 bursts.                                                                                                                                                                                                                                                       | specs/security/threat-model-SPEC-LGS-001-golden-signals.md        |
| Cost envelope                            | Per-container CPU/mem limits + documented aggregate ceiling (ENV-FR-11, ENV-NFR-07).                                                                                                                                                                                                                                                                                                               | ADR-0020                                                          |
| Pipeline security (SAST/SCA/secret/SBOM) | Images scanned (Trivy), SBOM produced, secret-scan on shipper/generator code (ENV-NFR-05).                                                                                                                                                                                                                                                                                                         | ADR-0029                                                          |

**STRIDE pass over the new untrusted-input boundaries:**

- **HAProxy log line → shipper (Tampering/DoS):** a crafted/oversized/malformed line must not crash
  the shipper (drop + counter, ENV-FR-03/§8); a flood is bounded by HAProxy's own rate and the
  shipper's batch/backpressure (ENV-FR-04).
- **Shipper → `/ingestion` (Spoofing/Injection):** a `path` containing `:`/`*`/newline/`gs:` must not
  enable key collision downstream — service-side URL-encoding (ADR-0068, `KeyInjectionTest`); the
  shipper must pass `path` through faithfully without partial decoding.
- **Information disclosure:** raw `client_ip` must not leak via shipper stdout, host-published ports
  (Redis not published), or unmasked store contents (PiiLeak scan, AC-06).

**AI Safety (Phase 10):** **N/A — no agent, guardrail, `action_type`, or autonomy is built or
touched here** (no `src/agents/`, no `src/guardrails/`). If a future change wires the Agentic layer
to this environment's `/analytics`, Phase 10 becomes mandatory then.

---

## 12. Acceptance Criteria _(gate: dry-run validation)_

<!-- EARS-style, observable/runnable. These become the dry-run evidence rows in /deliver's
     FINAL-REPORT. Several deliberately re-validate SPEC-LGS-001 ACs *at the environment level*,
     i.e. through the real HAProxy→shipper path rather than direct POSTs. -->

| ID    | Acceptance criterion (WHEN … THEN …)                                                                                                                                                                                                                                                                                                                                                                                                                                                           | Covers FR(s)                    |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------- |
| AC-01 | WHEN `make gs-up` runs from a clean checkout, THEN `docker compose --profile golden-signals ps` shows `haproxy`, `gs-log-shipper`, `golden-signals`, `redis` all **healthy**, and `GET /analytics/health` returns `200` (also satisfies SPEC-LGS-001 AC-01 at env level).                                                                                                                                                                                                                      | ENV-FR-01, ENV-FR-06            |
| AC-02 | WHEN `gs-traffic-generator` drives the `steady` scenario across ≥5 paths, THEN within one aggregation window `GET /analytics?path=&signal=latency&window=1m` returns **non-empty numeric P50/P95/P99** sourced from HAProxy traffic (env-level SPEC-LGS-001 AC-04).                                                                                                                                                                                                                            | ENV-FR-02, ENV-FR-03, ENV-FR-07 |
| AC-03 | WHEN steady traffic has run, THEN `GET /analytics/paths` lists every generated path (env-level SPEC-LGS-001 AC-05).                                                                                                                                                                                                                                                                                                                                                                            | ENV-FR-02, ENV-FR-03, ENV-FR-07 |
| AC-04 | WHEN N requests are logged by HAProxy for a monitored path under steady state with no retry, THEN the pipeline's traffic count for that path equals N (shipper delivery ratio = 100% ±0); under retry (§15-Q3 at-least-once), the count is N plus a bounded **documented over-count** and never less than N.                                                                                                                                                                                   | ENV-FR-03, ENV-FR-04            |
| AC-05 | WHEN the shipper presents a missing/invalid API key, THEN `/ingestion` returns `401` and the shipper fails fast with a clear diagnostic (no silent data loss masquerading as success).                                                                                                                                                                                                                                                                                                         | ENV-FR-03, ENV-FR-09            |
| AC-06 | WHEN traffic with known client IPs has been processed, THEN a scan of the Redis dump, the shipper stdout, and all container logs shows **no unmasked IP octet/hextet** (env-level SPEC-LGS-001 AC-03).                                                                                                                                                                                                                                                                                         | ENV-FR-12, ENV-NFR-04           |
| AC-07 | WHEN the `error-burst`/`latency-burst` scenario exceeds `RATE_LIMIT_PER_MINUTE`, THEN `/ingestion` returns `429`+`Retry-After` and the shipper backs off and retries within budget, dropping nothing under the burst's recoverable portion (env-level SPEC-LGS-001 AC-07).                                                                                                                                                                                                                     | ENV-FR-04                       |
| AC-08 | WHEN `redis` is restarted mid-run, THEN the environment recovers to healthy automatically and resumes ingestion, with data loss bounded to in-flight aggregates only (documented).                                                                                                                                                                                                                                                                                                             | ENV-FR-06, ENV-NFR-06           |
| AC-09 | WHEN the `latency-burst` scenario pushes P99 over `HITL_P99_LATENCY_MS`, THEN a subsequent `GET /analytics` `_governance` block shows `recommended_action_mode:"HITL"` and `human_approval_required:true` (env-level SPEC-LGS-001 AC-08).                                                                                                                                                                                                                                                      | ENV-FR-07                       |
| AC-10 | WHEN `make gs-demo` runs, THEN it exits `0` and emits a transcript asserting AC-01..AC-09; observed error rate is within ±2% of the generator's injected rate (env-level SPEC-LGS-001 AC-10).                                                                                                                                                                                                                                                                                                  | ENV-FR-07, ENV-FR-08            |
| AC-11 | WHEN the environment is started with only `.env.example` defaults plus the required `GS_API_KEYS`, THEN it reaches all-healthy with **no other manual config**, and a tree scan finds no committed secret.                                                                                                                                                                                                                                                                                     | ENV-FR-09                       |
| AC-12 | WHEN a single HAProxy request is traced, THEN the same `X-Trace-Id` is observable in the shipper's structured log line and in the service's audit entry for that ingestion (env-level NFR-03).                                                                                                                                                                                                                                                                                                 | ENV-NFR-03                      |
| AC-13 | WHEN `make gs-down` runs, THEN no profile container, named volume, or dedicated network remains (`docker ... ls` shows zero residue).                                                                                                                                                                                                                                                                                                                                                          | ENV-FR-10                       |
| AC-14 | WHEN the profile is up, THEN every container reports a non-zero CPU/memory limit and the documented aggregate ceiling is not exceeded under the `steady` scenario.                                                                                                                                                                                                                                                                                                                             | ENV-FR-11, ENV-NFR-07           |
| AC-15 | WHEN the profile is up, THEN `redis` is reachable from `golden-signals` on the internal `gs-net` network **but is NOT host-published** (a host-side `redis-cli`/TCP connect to the published-ports list finds no Redis), it requires `REDIS_PASSWORD` (an unauthenticated `PING` over the internal net returns `NOAUTH`/auth error), and `docker compose config` shows `RETENTION_1M_SECONDS` and `RETENTION_5M_SECONDS` resolved into the `golden-signals` container environment from `.env`. | ENV-FR-05                       |

> **Requirement coverage footer (gate).** **12 FRs** total (ENV-FR-01..12) · **12 mapped to ≥1 AC** ·
> **0 unmapped**. Phase-3 found **K=1** (ENV-FR-05 had zero ACs — `SPEC_DEVIATION`); **AC-15 added at
> Phase 4 closes it**, so the derived map is now exhaustive and the truthful coverage is **K = 0**.
> Per-FR map (re-derived Phase 4): FR-01→AC-01 · FR-02→AC-02/03 · FR-03→AC-02/03/04/05 · FR-04→AC-04/07 ·
> **FR-05→AC-15** · FR-06→AC-01/08 · FR-07→AC-02/03/09/10 · FR-08→AC-10 · FR-09→AC-05/11 · FR-10→AC-13 ·
> FR-11→AC-14 · FR-12→AC-06. (ENV-NFR-03/04/06/07 additionally covered by AC-12/06/08/14.) Every FR maps
> to at least one AC; `K = 0`, so the Definition-of-Ready coverage gate is satisfied.

> The table above is the canonical, machine-traceable AC form. A Gherkin companion (per
> `docs/product/acceptance-criteria-standard.md`) is optional; each `Scenario` would tag an `AC-NN`
> and this table stays authoritative.

---

## 13. Risks & Limitations

- **HAProxy log fidelity.** `%Tt` is _total_ time (incl. client-side) and overstates server-side
  latency vs the article's intent. **Resolved (§15-Q4):** `%Tr` (server response time) is the
  Golden-Signals latency field, `%Tt` captured for context; finalised in PROPOSED ADR-0084.
- **At-least-once shipping vs exact counts.** Retry (ENV-FR-04) can re-deliver a batch on an ambiguous
  `5xx`; without idempotency the traffic count could double-count. **Resolved (§15-Q3):** at-least-once
  with idempotent batch ids, accepting a documented small over-count under retry; budget pinned in
  PROPOSED ADR-0084. AC-04's "= N (±0)" holds absent retry; under retry, count is N + a bounded
  documented over-count.
- **Demonstration ≠ production.** Single-node Redis, ephemeral persistence, laptop-scale limits — this
  rig proves correctness and the MTTD/MTTR _narrative_, not production throughput (SPEC-LGS-001 §13).
- **Synthetic traffic realism.** A deterministic generator cannot reproduce real production
  distributions; percentiles demonstrate the _mechanism_, not field-representative numbers.
- **Bespoke shipper maintenance.** **Resolved (§15-Q2):** a bespoke Python forwarder is chosen over an
  off-the-shelf shipper (Vector/Fluent Bit); this trades a config-DSL + extra supply-chain surface for
  first-party maintenance of the parse/normalise/ship code (pinned deps, ENV-NFR-05/08).
- **No runnable service image yet (Phase-0 carry-over).** ADR-0066's Java image is not yet built, so
  §8 contracts run against a stub/mock and the live `gs-demo` acceptance (AC-01/AC-10) is
  deferred-and-logged until the image exists.

---

## 14. ADR & Dependency Impact

- **Reuses:** ADR-0003 (async/decoupling), ADR-0011 (HITL/HOTL), ADR-0012 (PII), ADR-0020 (cost),
  ADR-0026 (audit), ADR-0029 (DevSecOps), ADR-0066 (Java runtime — the service image), ADR-0067
  (Redis store), ADR-0068 (extraction rules — the HAProxy fields), ADR-0069 (intra-service queue).
- **Adds (`new_adrs_required`):** `haproxy-log-shipping-bridge` (HAProxy log-format + parse/normalise
  /batch/retry/idempotency + timing-field choice), `golden-signals-compose-environment` (the
  `golden-signals` profile, `gs-net` isolation, depends_on health graph, resource limits, Redis
  persistence policy), `demonstration-traffic-generator` (deterministic scenarios + assertions).
- **Produces:** `docker-compose.yml` `golden-signals` profile + `infrastructure/golden-signals/`
  (haproxy.cfg, shipper config/image, generator), Makefile targets (`gs-up/gs-demo/gs-smoke/gs-down`),
  `.env.example` additions, `dependency-manifest.yaml` + SBOM for shipper/generator, an
  `environment:` block in `docs/sre/slo/golden-signals-slo.yaml`, and a runbook stub
  (`docs/runbooks/golden-signals-environment.md`).
- **Touches `services.yaml`?** No new service entry required — `gs-log-shipper`/`gs-traffic-generator`
  are environment scaffolding, not first-class API services; record them under
  `infrastructure/golden-signals/` and CODEOWNERS, not as `services.yaml` APIs. _(Confirm at the
  architecture gate — see §15-Q5.)_

---

## 15. Open Questions — RESOLVED (Phase 4)

<!-- Resolved at HITL gates, not assumed. All six resolved at Phase 4 Specification. Items marked
     DECIDED are final; items marked RECOMMENDED are proposals for the tech_lead + security_lead to
     confirm at the approval gate, and are finalised in the named PROPOSED ADR at Phase 5. -->

1. **Article-vs-ADR-0066 runtime.** The source article specifies **Python/FastAPI + Celery/RQ**; the
   repo's binding decision (ADR-0066) builds `golden-signals` in **Java 21/Spring Boot**. This
   environment is deliberately language-neutral (treats the service as a §8 black box).
   **Resolution — DECIDED:** keep **ADR-0066 (Java 21 / Spring Boot)**; the service is a black box
   honouring the SPEC-LGS-001 §8 HTTP contract. **No superseding ADR is opened.** No runnable Java
   image exists yet, so §8 contracts are exercised against a stub/mock and the live `gs-demo`
   acceptance (AC-01/AC-10) remains deferred-and-logged per Phase 0. _Rationale: honours the binding
   ADR; the article's language choice is satisfied at the contract level, not the implementation
   language. Affects only which prebuilt image the compose profile references._
2. **Shipper implementation.** Off-the-shelf (Vector / Fluent Bit + transform) vs a small
   purpose-built forwarder. **Resolution — DECIDED:** a **bespoke Python forwarder** (`gs-log-shipper`).
   _Rationale: keeps the parse → normalise → batch → ship contract in first-party code under repo
   governance/test, avoids a heavyweight DSL + extra supply-chain surface for a demonstration rig.
   Detailed design lands in the PROPOSED `haproxy-log-shipping-bridge` ADR (ADR-0084) at Phase 5._
3. **Delivery semantics.** At-least-once with idempotent batch ids vs at-most-once with a documented
   loss budget. **Resolution — RECOMMENDED (pending Phase-4 confirmation; finalised in PROPOSED
   ADR-0084):** **at-least-once with idempotent batch ids**, accepting a small **documented**
   over-count under retry. _Rationale: never silently drop (ENV-FR-04); idempotent ids bound
   double-counting; an explicit over-count budget is preferable to data loss for a Golden-Signals rig.
   Reconcile AC-04's "= N (±0)" wording to "±0 absent retry; bounded documented over-count under
   retry" when ADR-0084 fixes the budget._
4. **HAProxy timing field.** `%Tt` (total) vs `%Tr`/`%Ta` as `response_time_ms`. **Resolution —
   RECOMMENDED (pending Phase-4 confirmation; finalised in PROPOSED ADR-0084):** emit **`%Tr` (server
   response time)** as the Golden-Signals latency field, and **also capture `%Tt`** for context.
   _Rationale: `%Tt` is total time including the client side and overstates server-side latency, which
   is the article's intent. ENV-FR-02, §9.1 and §10(a) are reconciled in this revision to `%Tr` (with
   `%Tt` retained as a context field)._
5. **Generator/shipper as services?** Register `gs-traffic-generator` / `gs-log-shipper` in
   `services.yaml`, or keep them as environment scaffolding. **Resolution — RECOMMENDED (confirm at
   the Phase-5 architecture gate):** **NO new `services.yaml` entry.** They are environment scaffolding
   under `infrastructure/golden-signals/`, governed via `.github/CODEOWNERS` (added at Phase 6), per
   §14. _Rationale: they are not first-class API services (no public API, no Kafka topic, no K8s
   deployment); `services.yaml` is reserved for those (CLAUDE.md §0.1)._
6. **Redis persistence in the rig.** Ephemeral (clean `gs-down`) vs `--save`. **Resolution — DECIDED:**
   **ephemeral default (no `--save`)** per §9.3, so `make gs-down` leaves no historical data. AC-08
   recovery is tested via an explicit Redis restart, not via persistence.

> **All six open questions are resolved.** DECIDED: Q1, Q2, Q6 (final). RECOMMENDED (human to confirm
> at the approval gate; finalised in PROPOSED ADRs at Phase 5): Q3, Q4 (→ ADR-0084), Q5 (→ Phase-5
> architecture gate). No `[HITL-ESCALATE]` triggered — no guardrail/flag/ADR-count threshold crossed.

---

## 16. References

- Souza Jr., V. de O. _Log-Based Golden Signals: A Scalable Ingestion and Predictive Analytics
  Infrastructure for Agentic AI Copilots._ PPGCA/Unisinos, 2026 — **§3.4 (environment & stack)** is
  the direct source for this spec.
- `specs/system/SPEC-LGS-001-log-based-golden-signals.md` — the application this environment runs.
- `specs/features/SPEC-LGS-001-golden-signals-feature-spec.md` — the Java/Spring Boot component design.
- `specs/security/threat-model-SPEC-LGS-001-golden-signals.md` — STRIDE baseline.
- ADR-0066 (runtime stack), ADR-0067 (Redis store), ADR-0068 (extraction rules), ADR-0069 (queue),
  ADR-0003 (async), ADR-0011/0012/0020/0026/0029 (governance/privacy/cost/audit/DevSecOps).
- Beyer, B. et al. _Site Reliability Engineering._ O'Reilly, 2016 — Golden Signals.
- Dean, J.; Barroso, L. A. _The tail at scale._ CACM 56(2), 2013 — percentile rationale.
- `docs/privacy/data-processing-register.md` — Activity 6 (this environment's `client_ip` processing path).

---

## 17. Spec Changelog

| Version | Date       | Phase           | Change                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ------- | ---------- | --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0.1.0   | 2026-06-16 | 3 Grooming      | Initial draft authored; DoR 13/16; Phase-3 audit found `SPEC_DEVIATION` K=1 (ENV-FR-05 unmapped to any AC).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| 0.2.0   | 2026-06-16 | 4 Specification | **(1)** Moved to canonical path `specs/infrastructure/`. **(2)** Added **AC-15** mapping ENV-FR-05 (Redis internal-only, `REDIS_PASSWORD`, retention env wiring); §12 footer corrected to truthful **K=0** with per-FR map. **(3)** Resolved all six §15 open questions (Q1/Q2/Q6 DECIDED; Q3/Q4/Q5 RECOMMENDED pending human confirm). **(4)** Set the four §10 env SLO objectives to proposed values (`RECOMMENDED`). **(5)** Recorded lightweight DPIA note as Activity 6 in the data-processing register; §11 PII row updated. **(6)** Reconciled the latency field to `%Tr` (Q4) across ENV-FR-02 / §8 / §9.1 / §10(a) / §13; reconciled AC-04 to at-least-once (Q3). PROPOSED ADR numbers 0084/0085/0086 labelled in `new_adrs_required`. Status set to `in-review`. **Approval (`approved`) is the pending human gate (tech_lead + security_lead).** |
