# ADR-0086 — Demonstration Traffic Generator (`gs-traffic-generator`)

**Status:** Accepted <!-- accepted 2026-06-16 by valdomirosouza (Tech Lead), Refs #18, #21 -->
**Date:** 2026-06-16
**Authors:** Valdomiro Souza
**Reviewers:** Tech Lead
**Spec:** [SPEC-LGS-002](../../specs/infrastructure/SPEC-LGS-002-golden-signals-environment.md) — §7, §8, §12 (AC-09), §15
**Relates to:** [ADR-0084](ADR-0084-haproxy-log-shipping-bridge.md) (the bridge it ultimately feeds via HAProxy), [ADR-0085](ADR-0085-golden-signals-compose-environment.md) (the profile it runs in), [ADR-0011](ADR-0011-hitl-hotl-model.md) (the HITL flip it exercises), [ADR-0066](ADR-0066-spec-lgs-001-runtime-stack-java-spring-boot.md) (the service whose thresholds it pushes — black box)
**Refs:** #18 (epic), #21 (B-07)
**Scope:** `SPEC-LGS-002` demonstration environment only — the synthetic load source that drives the HAProxy listener so the pipeline produces observable Golden Signals. Does **not** assert anything about the service's internals (black box per ADR-0066).

---

## Context

The Golden-Signals rig only produces meaningful signals if something drives traffic through it.
SPEC-LGS-002 §7 names `gs-traffic-generator` as the component that issues HTTP requests at the
**HAProxy listener**, so HAProxy writes genuine access-log lines that flow HAProxy →
`gs-log-shipper` (ADR-0084) → `POST /ingestion` → `golden-signals`. The generator is a _log source
driver_, not a component under test.

For the rig to be a credible **demonstration**, the generated load must be:

- **reproducible** — the same invocation yields the same path mix and the same signal shape, so
  acceptance (AC-02/AC-03/AC-09) is deterministic rather than flaky;
- **multi-path** — it must drive **≥5 distinct request paths** so `GET /analytics/paths` and
  per-path percentiles have real content (AC-03);
- **able to push the pipeline past SPEC-LGS-001's FR-13 HITL thresholds on demand** — otherwise the
  rig cannot demonstrate the most important governance behaviour (the HITL flip), which is the
  point of AC-09.

This ADR records the generator's design decisions so the container is built from a binding record.

## Decision

We will implement `gs-traffic-generator` as a **deterministic, seeded Python load driver** that
issues HTTP requests at the HAProxy listener (LANGUAGE = Python for this delivery).

1. **Deterministic / seeded.** The generator is driven by a fixed seed (env-configurable) so a given
   `(seed, scenario, duration)` produces the same request sequence and the same resulting signal
   shape on every run. This makes AC-02/AC-03/AC-09 deterministic and reproducible (ENV-NFR-01), and
   makes traffic-count assertions (AC-04: delivered = N) meaningful.

2. **Drives ≥5 paths via HAProxy (AC-03).** The generator issues requests across **at least five
   distinct request paths** at the HAProxy listener only — never directly at `gs-log-shipper` or the
   service. HAProxy fronts a trivial upstream (or canned responses) purely so it emits genuine
   access-log lines across those paths (per ADR-0085 / SPEC-LGS-002 §7). It is fire-and-measure: the
   generator does not consume `/analytics` (no functional return path).

3. **Scenario flag `GS_DEMO_SCENARIO ∈ {steady, latency-burst, error-burst}`.** A single environment
   variable selects the load profile:
   - **`steady`** — balanced multi-path traffic at a nominal rate; establishes non-empty P50/P95/P99
     per path within one aggregation window (AC-02) and the full path list (AC-03). Counts are exact
     absent retry (AC-04).
   - **`latency-burst`** — deliberately drives **server response time (`%Tr`) past the service's
     p99 latency HITL threshold** so the service's `_governance` block flips to
     `recommended_action_mode: HITL` / `human_approval_required: true` (FR-12/FR-13, ADR-0011).
   - **`error-burst`** — deliberately drives **the error rate past the service's error-rate HITL
     threshold** to flip the same governance backstop.
     The thresholds themselves live in the service's `golden-signals-slo.yaml hitl_triggers`
     (p99 1000 ms, error*rate 0.05) and are **unchanged** by this ADR — the generator's job is only to
     \_reach* them so the flip is _observable_, capturing the AC-09 demonstration evidence.

4. **Scenarios exist to push past SPEC-LGS-001 HITL thresholds (AC-09).** The non-`steady` scenarios
   are intentionally adversarial-to-the-thresholds: their reason for existing is to demonstrate that
   a latency/error breach correctly flips the service into the HITL backstop. This is a
   _demonstration_ of the existing FR-13 control, **not** a change to it — the generator never alters
   any threshold, flag, or governance setting (those would be human/governance-gated, CLAUDE.md §3.3,
   ADR-0015).

5. **Synthetic, masked-only data.** All generated client IPs and payloads are synthetic; no real PII
   is used (CLAUDE.md §3.1). `client_ip` masking remains the service's responsibility (FR-02,
   ADR-0012). DPIA was waived at Phase 2 for synthetic + masked data (Activity 6; SPEC-LGS-002 §11).

6. **Runs inside the `golden-signals` profile (ADR-0085) under resource limits.** The generator is a
   profile container with its own CPU/memory limits (ENV-FR-11) and is _not_ host-published; it
   reaches HAProxy over `gs-net`. It is **not** a first-class service (no `services.yaml` entry —
   confirmed in ADR-0085 §7); governed via `infrastructure/golden-signals/` CODEOWNERS at Phase 6.

## Consequences

### Positive

- Deterministic, seeded load makes the whole acceptance suite (AC-02/03/04/09) reproducible rather
  than flaky — the rig demonstrates the _mechanism_ repeatably.
- The three scenarios turn the rig into a self-contained demonstration of the _most governance-
  critical_ behaviour: that a latency or error breach flips the service into the HITL backstop
  (AC-09), without any human having to hand-craft load.
- No new service surface and no threshold mutation — the generator is a pure log-source driver that
  exercises existing controls.

### Negative / Trade-offs

- **Synthetic traffic is not field-representative (the §13 risk).** A deterministic generator cannot
  reproduce real production distributions; the percentiles it produces demonstrate the _mechanism_,
  not field-representative numbers. Accepted and documented.
- **Scenario-tuned bursts are coupled to the current threshold values.** If the service's
  `hitl_triggers` values change, the `latency-burst`/`error-burst` magnitudes may need re-tuning to
  still cross them. Mitigated by deriving burst magnitudes relative to the documented thresholds
  rather than hard-coding absolute numbers where practical.

### Neutral

- The generator runs against HAProxy and never against the service or shipper directly, so it stays
  agnostic to ADR-0084's batching/retry and to the service's internals (ADR-0066 black box).
- Whether HAProxy proxies a trivial upstream or returns canned responses is an ADR-0085 / compose
  detail, transparent to the generator.

## Alternatives Considered

- **Non-deterministic / random load (e.g. raw `hey`/`wrk` with random paths).** Rejected — makes
  AC-02/03/04/09 non-reproducible and turns count assertions into flaky tests; a seeded generator is
  required for a deterministic demonstration.
- **Single-path load.** Rejected — would leave `GET /analytics/paths` and per-path percentiles
  trivial; ≥5 paths is required to demonstrate the per-path Golden-Signals breakdown (AC-03).
- **Generator flips the HITL threshold directly to force the demo.** Rejected outright — changing a
  threshold/flag is a governance-gated autonomy change (CLAUDE.md §3.3, ADR-0015); the correct
  demonstration _reaches_ the existing threshold with real load, it does not lower the bar.
- **Drive `gs-log-shipper` or `/ingestion` directly, bypassing HAProxy.** Rejected — it would bypass
  the very log-source → shipper boundary the rig exists to demonstrate and would not produce genuine
  HAProxy access logs.

## Compliance & Risk

- **Controls affected:** none weakened. The generator _exercises_ the FR-13 HITL backstop (ADR-0011)
  and the ingestion auth/rate-limit path; it changes no control. Its requests form a new (synthetic)
  source on the HAProxy boundary already covered by the threat-model delta (Refs #22).
- **Data classification impact:** none — synthetic data only; no real PII (CLAUDE.md §3.1); masking
  stays the service's responsibility (ADR-0012). DPIA waived for synthetic + masked data (Activity 6).
- **Autonomy impact:** none — the generator performs no autonomous outward action and changes no
  feature flag, threshold, or autonomy setting; it only _triggers_ the service's existing HITL flip.
- **Review/expiry:** scoped to SPEC-LGS-002; re-tune burst magnitudes if the service's HITL
  thresholds change.

---

## Related

- `docs/adr/README.md` — master index & lifecycle definition
- `docs/adr/adr-review-checklist.md` — checklist to apply before marking this ADR `Accepted`
- [SPEC-LGS-002](../../specs/infrastructure/SPEC-LGS-002-golden-signals-environment.md) — §7/§8/§12 (AC-09)/§15
- [ADR-0084](ADR-0084-haproxy-log-shipping-bridge.md) — the bridge that ships the traffic this generator produces
- [ADR-0085](ADR-0085-golden-signals-compose-environment.md) — the compose profile this generator runs in
- [ADR-0011](ADR-0011-hitl-hotl-model.md) — the HITL/HOTL model whose flip the scenarios demonstrate
