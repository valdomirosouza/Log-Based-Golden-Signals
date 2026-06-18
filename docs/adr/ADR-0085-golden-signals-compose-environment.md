# ADR-0085 — Golden-Signals Compose Environment (`golden-signals` profile)

**Status:** Accepted <!-- accepted 2026-06-16 by valdomirosouza (Tech Lead), Refs #18, #20 -->
**Date:** 2026-06-16
**Authors:** Valdomiro Souza
**Reviewers:** Tech Lead
**Spec:** [SPEC-LGS-002](../../specs/infrastructure/SPEC-LGS-002-golden-signals-environment.md) — §7, §8, §9.3, §11, §14, §15-Q5/Q6
**Relates to:** [ADR-0020](ADR-0020-finops-cost-allocation.md) (cost/FinOps envelope), [ADR-0019](ADR-0019-redis-tls-value-encryption.md) (Redis posture), [ADR-0066](ADR-0066-spec-lgs-001-runtime-stack-java-spring-boot.md) (service runtime — black box), [ADR-0067](ADR-0067-redis-as-timeseries-store.md) (Redis TTL retention), [ADR-0084](ADR-0084-haproxy-log-shipping-bridge.md) (the bridge this profile runs)
**Refs:** #18 (epic), #20 (B-06)
**Scope:** `SPEC-LGS-002` demonstration environment only — the Docker Compose `golden-signals` profile that wires HAProxy, `gs-log-shipper`, the `golden-signals` service (black box), and Redis on one isolated network. Does **not** change the service or its data model.

---

## Context

SPEC-LGS-002 §7 requires a reproducible, single-command demonstration environment that brings up
four components — `haproxy`, `gs-log-shipper` (ADR-0084), the `golden-signals` service
(Java/Spring Boot black box per ADR-0066), and `redis` — to an all-healthy state with no manual
intervention (ENV-FR-01). The environment exists to **demonstrate** the log-based Golden-Signals
mechanism on a developer laptop / CI runner, not to run at production throughput (§13).

Several environment-topology decisions need an architecture record so the compose file is generated
_from_ a binding decision rather than hand-drifted (SPEC-LGS-002 §8 warns against hand-drift):
network isolation and host exposure (ENV-FR-12), per-container resource limits within a cost
envelope (ENV-FR-11, ADR-0020), image pinning (ENV-NFR-01), health-ordered startup (ENV-FR-01),
Redis persistence policy (§9.3, §15-Q6), and whether the new helper components warrant a
`services.yaml` entry (§15-Q5, confirmed at _this_ architecture gate).

## Decision

We will define a Docker Compose **`golden-signals` profile** under `infrastructure/golden-signals/`
with the following topology. (Implementation lands at Phase 6; this ADR pins the decisions.)

1. **Single isolated bridge network `gs-net` (ENV-FR-12).** All four components attach to one
   internal Docker bridge network. **Host-publish ONLY** the HAProxy listener port and the
   `golden-signals` API port **8085**. **Redis is NOT host-published** in the default profile —
   it is reachable by the `golden-signals` service over `gs-net` only. This satisfies AC-15 (a
   host-side `redis-cli`/TCP connect finds no published Redis port) and keeps the only externally
   reachable surfaces the HAProxy listener and the analytics API.

2. **Per-container CPU & memory limits _and_ reservations (ENV-FR-11, ADR-0020 cost envelope).**
   Every container in the profile declares explicit `deploy.resources.limits` and `.reservations`
   for CPU and memory. The **aggregate ceiling for the whole profile is documented** (ENV-NFR-07)
   and sized to fit a developer laptop / CI runner, recorded against the ADR-0020 cost envelope.
   This satisfies AC-14 (every container reports a non-zero limit; the aggregate ceiling is not
   exceeded under the `steady` scenario).

3. **Pinned image tags — no `latest` (ENV-NFR-01).** Every built or pulled image is pinned to an
   explicit tag so a clean checkout reproduces the environment bit-for-bit. No image uses the
   floating `latest` tag.

4. **Health-ordered startup via `depends_on` health conditions (ENV-FR-01).** Startup is gated on
   health, not bare container start: Redis healthy → `golden-signals` healthy
   (`GET /analytics/health` → `200`) → `gs-log-shipper` and `haproxy` start once their dependencies
   report healthy. `depends_on: { condition: service_healthy }` enforces the ordering so the
   profile reaches all-healthy with no manual intervention (AC-01); a service that fails its
   healthcheck holds the startup gate (§8 — `503` on `/analytics/health` marks the container
   unhealthy).

5. **Ephemeral Redis by default — no `--save` (§9.3, §15-Q6, DECIDED).** Redis runs **ephemeral**
   in the demonstration profile (no `--save` / no persistence), so `make gs-down` leaves no
   historical data and AC-13 (zero residue) holds. Retention is still enforced _inside_ the
   service via TTL (ADR-0067), driven by env vars the environment wires
   (`RETENTION_1M_SECONDS=7200`, `RETENTION_5M_SECONDS=86400`); the **environment adds no Redis
   persistence policy** beyond choosing ephemeral. Resilience under a Redis restart is bounded,
   documented in-flight-aggregate loss only (ENV-NFR-06).

6. **Reuse the existing repo `redis` service (no second Redis implementation).** The profile reuses
   the repo's already-declared `redis` service image/definition exactly as registered, rather than
   introducing a parallel Redis. The environment provisions Redis to hold the service's keys but
   defines none of them (key grammar is owned by ADR-0068). Redis requires `REDIS_PASSWORD`
   (an unauthenticated `PING` over the internal net returns a `NOAUTH`/auth error — AC-15).

7. **`services.yaml` confirmation (§15-Q5, CONFIRMED at this gate).** `gs-log-shipper` and
   `gs-traffic-generator` get **NO `services.yaml` entry**. They are environment _scaffolding_, not
   first-class API services — no public API, no Kafka topic, no Kubernetes deployment — so they fall
   outside the `services.yaml` canonical service registry (CLAUDE.md §0.1). They are governed
   instead via a `.github/CODEOWNERS` entry for `infrastructure/golden-signals/`, **added at Phase 6**
   (this architecture phase does not edit `services.yaml` or `CODEOWNERS`). This confirms the
   Phase-4 RECOMMENDED disposition as the binding architecture decision.

8. **Service stays a black box.** This ADR wires the environment around the `golden-signals`
   service via its §8 contract (`POST /ingestion`, `GET /analytics*`, `GET /analytics/health`); it
   adds no intra-service architecture. No runnable Java image exists yet (Phase-0 carry-over), so
   §8 contracts run against a stub/mock until ADR-0066's image is built; live `gs-demo` acceptance
   (AC-01/AC-10) remains deferred-and-logged per Phase 0 (SPEC-LGS-002 §13, §15-Q1).

## Consequences

### Positive

- One-command, reproducible (pinned-tag) environment that reaches all-healthy deterministically via
  health-ordered startup — satisfies ENV-FR-01 / AC-01 / AC-11.
- Tight blast radius: only the HAProxy listener and `:8085` are host-reachable; Redis is internal
  and password-protected, closing the most obvious data-exposure path (AC-15).
- Bounded, documented cost: explicit per-container limits + a documented aggregate ceiling keep the
  rig laptop/CI-sized within the ADR-0020 envelope (AC-14).
- Clean teardown: ephemeral Redis + named-volume removal means `make gs-down` leaves zero residue
  (AC-13).
- Reusing the existing `redis` service avoids a divergent second Redis to scan, patch, and operate.

### Negative / Trade-offs

- **Ephemeral Redis loses in-flight aggregates on restart** — accepted and documented (ENV-NFR-06);
  this is a demonstration rig, not a durable store. Operators wanting persistence across `gs-down`
  must opt into `--save`, which then defeats AC-13's zero-residue guarantee.
- **Single-node, laptop-scale** — the profile proves the _mechanism_ and the MTTD/MTTR narrative,
  not production throughput (§13).
- **Helper components live outside `services.yaml`** — discoverability of `gs-log-shipper` /
  `gs-traffic-generator` relies on `infrastructure/golden-signals/` + CODEOWNERS rather than the
  service registry; accepted because they are not API/topic/deployment surfaces (Q5).

### Neutral

- Containerisation and the REST contract are language-agnostic; the service runtime (Java, ADR-0066)
  and the bridge runtime (Python, ADR-0084) are independent of this topology decision.
- Retention values are env-wired but owned by the service's TTL logic (ADR-0067); the environment
  sets no key and no TTL of its own.

## Alternatives Considered

- **Host-publish Redis for convenience.** Rejected — needlessly exposes the datastore; violates
  ENV-FR-12 and AC-15. Internal-only + password is the secure default; developers can attach via
  `docker compose exec` when they genuinely need a Redis shell.
- **Persistent Redis (`--save`) by default — §15-Q6.** Rejected for the default profile: it leaves
  residue after `gs-down` (breaks AC-13) and adds no value to a demonstration rig that regenerates
  synthetic traffic on demand. Ephemeral is the decided default; persistence is an opt-in.
- **A dedicated second Redis instance for the rig.** Rejected — duplicates an already-declared,
  already-scanned `redis` service; reuse keeps the supply-chain and operational surface smaller.
- **Register the helpers in `services.yaml` — §15-Q5.** Rejected — they expose no public API, Kafka
  topic, or K8s deployment, so a registry entry would misrepresent them as first-class services
  (CLAUDE.md §0.1). CODEOWNERS over `infrastructure/golden-signals/` is the proportionate governance.
- **Bare `depends_on` (start-order only, no health condition).** Rejected — start order without a
  health gate races the service against an unready Redis and fails ENV-FR-01's no-manual-intervention
  all-healthy requirement.

## Compliance & Risk

- **Controls affected:** network exposure (ENV-FR-12) and the cost envelope (ENV-FR-11, ADR-0020).
  No change to the service's auth, data flow, or data model. The new untrusted-input boundaries
  introduced by the rig are recorded in the threat-model delta (Refs #22), not here.
- **Data classification impact:** none added by the topology — `client_ip` (telemetry-L2 PII) is
  masked by the service (ADR-0012); ephemeral Redis shortens the at-rest exposure window. Redis
  posture follows ADR-0019 (password; not internet-exposed).
- **Autonomy impact:** none — no HITL/HOTL behaviour or feature flag is changed by the environment.
- **Review/expiry:** scoped to SPEC-LGS-002; revisit if the rig is promoted toward a persistent or
  multi-node deployment.

---

## Related

- `docs/adr/README.md` — master index & lifecycle definition
- `docs/adr/adr-review-checklist.md` — checklist to apply before marking this ADR `Accepted`
- [SPEC-LGS-002](../../specs/infrastructure/SPEC-LGS-002-golden-signals-environment.md) — §7/§9.3/§11/§14/§15-Q5/Q6
- [ADR-0084](ADR-0084-haproxy-log-shipping-bridge.md) — the log-shipping bridge run by this profile
- [ADR-0086](ADR-0086-demonstration-traffic-generator.md) — the generator container in this profile
- [ADR-0067](ADR-0067-redis-as-timeseries-store.md) — Redis TTL retention the env wires (not owns)
