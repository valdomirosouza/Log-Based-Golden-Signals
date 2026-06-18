# ADR-0084 — HAProxy Log-Shipping Bridge (`gs-log-shipper`)

**Status:** Accepted <!-- accepted 2026-06-16 by valdomirosouza (Tech Lead), Refs #18, #19; amended 2026-06-17 (Refs #28) — runtime transport edge added below -->
**Date:** 2026-06-16
**Authors:** Valdomiro Souza
**Reviewers:** Tech Lead
**Spec:** [SPEC-LGS-002](../../specs/infrastructure/SPEC-LGS-002-golden-signals-environment.md) — §7, §8, §9.1, §13, §15-Q2/Q3/Q4
**Relates to:** [ADR-0003](ADR-0003-async-api-strategy.md) (async/decoupling), [ADR-0012](ADR-0012-pii-masking-strategy.md) (PII), [ADR-0066](ADR-0066-spec-lgs-001-runtime-stack-java-spring-boot.md) (service runtime — black box), [ADR-0068](ADR-0068-golden-signal-extraction-rules.md) (extraction fields / key grammar), [ADR-0069](ADR-0069-queue-implementation.md) (intra-service queue)
**Refs:** #18 (epic), #19 (B-05)
**Scope:** `SPEC-LGS-002` demonstration environment only — the bridge between the HAProxy log source and the `golden-signals` `POST /ingestion` contract. Does **not** touch `golden-signals` internals (that service stays a §8 black box per ADR-0066).

---

## Context

The source article for the Log-Based Golden Signals work shows an arrow from HAProxy to the
ingestion service but never specifies the component that turns an HAProxy access-log line into the
canonical `POST /ingestion` JSON batch the service consumes. SPEC-LGS-002 §7 names that missing
component **`gs-log-shipper`** and pins its _contract_ (input log-line format → output JSON schema,
§8/§9.1) while leaving its _implementation_ to this architecture phase.

The shipper sits on a **new untrusted-input boundary**: HAProxy emits lines the shipper must parse,
normalise, batch, and deliver to an authenticated HTTP endpoint that can apply backpressure
(`429`/`503`). Three decisions are load-bearing and were surfaced as SPEC-LGS-002 §15 open
questions, resolved at the Phase-4 human sign-off gate (spec APPROVED v0.2.0, Refs #18):

- **Q2 — implementation form.** Off-the-shelf shipper (Vector / Fluent Bit + transform) vs a small
  purpose-built forwarder. _Resolved: bespoke._
- **Q3 — delivery semantics.** At-least-once with idempotent batch ids vs at-most-once with a loss
  budget. _Resolved: at-least-once._
- **Q4 — HAProxy timing field.** `%Tt` (total, includes client side) vs `%Tr` (server response
  time) as the Golden-Signals latency sample. _Resolved: `%Tr` as the signal, `%Tt` for context._

This ADR records those resolutions as a binding decision and pins the delivery-semantics budget so
SPEC-LGS-002 AC-04 has a concrete reconciliation. It aligns the bridge's async/decoupling and
delivery posture with **ADR-0003** and maps the output onto **SPEC-LGS-001 §8** (the HTTP contract)
and **§9.1** (the canonical entry), which the shipper reuses verbatim as just another `/ingestion`
client.

## Decision

We will implement `gs-log-shipper` as a **bespoke Python forwarder** with the following design.

1. **Implementation form (Q2) — bespoke Python forwarder.** A small first-party Python service
   under `infrastructure/golden-signals/` implements _parse → normalise → batch → ship_. We reject
   an off-the-shelf shipper (Vector/Fluent Bit) for this demonstration rig: a bespoke forwarder
   keeps the boundary contract in repo-governed, repo-tested code (pinned deps, ENV-NFR-05/08),
   and avoids a heavyweight config DSL plus the extra supply-chain surface of a third-party agent.
   Language is **Python** (LANGUAGE for this delivery); the service it feeds stays Java per ADR-0066.

2. **HAProxy log-format & timing field (Q4) — `%Tr` is the latency signal, `%Tt` is context.**
   HAProxy is configured with a **pinned `log-format`** emitting every field the four Golden
   Signals require (ENV-FR-02): request path, method, HTTP status (`%ST`), **server response time
   `%Tr` (ms) as the Golden-Signals latency field**, **total time `%Tt` additionally captured for
   context**, bytes sent (`%B`), client IP (`%ci`), backend (`%b`), and a timestamp. `%Tr` is chosen
   over `%Tt` because `%Tt` includes client-side time and would inflate server-side latency, which
   is contrary to the Golden-Signals intent (SPEC-LGS-002 §13, §9.1). The field map onto the
   SPEC-LGS-001 §9.1 canonical entry is the §9.1 table verbatim — notably `timestamp` is converted
   to **epoch-milliseconds**, and `path` is passed through **faithfully without partial decoding**
   so the service's downstream URL-encoding (ADR-0068) is the single, unambiguous key-encoding step.

3. **Delivery semantics (Q3) — at-least-once with idempotent batch ids.** Each shipped batch carries
   a deterministic **idempotent batch id**. Delivery is **at-least-once**: the shipper never silently
   discards an entry (ENV-FR-04). On an ambiguous `5xx` (or a `429`) the shipper retries the _same_
   batch with the _same_ batch id, so a retry that the service already partially applied can be
   recognised/deduplicated rather than blindly re-aggregated. We **accept a small, documented
   over-count** under retry as the explicit trade for never losing data on an ambiguous failure.
   - **Over-count budget:** bounded by the retry budget — at most `(max_retries)` re-deliveries of a
     single in-flight batch per ambiguous failure, and only for the window in which the failure
     occurred. Steady-state with no retry is exact (delivery ratio = N, ±0). This reconciles
     SPEC-LGS-002 **AC-04**: count is **`= N` absent retry**, and **`N` plus a bounded documented
     over-count, never less than `N`, under retry**.

4. **Bounded retry honouring `Retry-After` (ENV-FR-04).** On `429`/`503` the shipper applies
   **bounded** retry with backoff, honouring the `Retry-After` header when present. After the retry
   budget is exhausted, any still-undelivered entries are **counted** (`gs_shipper_*` drop counter),
   never silently dropped. On `401` the shipper **fails fast** with a clear diagnostic (no silent
   data loss masquerading as success — AC-05). On `422` (bad batch) it logs and drops the batch with
   a parse/validation counter. An unparseable HAProxy line is dropped with
   `gs_shipper_parse_errors_total` incremented and the shipper never crashes (§8).

5. **Alignment with ADR-0003 (async/decoupling).** The shipper decouples log emission from ingestion
   exactly as ADR-0003 prescribes for high-volume flows: HAProxy emission, in-shipper batching, and
   HTTP delivery are decoupled stages with backpressure handled at the delivery boundary
   (`429`/`503` → bounded retry), not by blocking the log source. The at-least-once + idempotent-id
   posture is the same delivery-semantics stance ADR-0003 takes for the broker path, applied here to
   the HTTP `/ingestion` boundary.

6. **PII & trace posture (delegated, not re-implemented).** `client_ip` is **telemetry-L2 / PII**;
   it is masked **by the service** before any persist/log (SPEC-LGS-001 FR-02, ADR-0012). The shipper
   transmits it only over the internal `gs-net` network and **must not write raw `client_ip` to its
   own stdout** (ENV-NFR-04). The shipper sets/propagates **`X-Trace-Id`** on each `POST /ingestion`
   so a request is correlatable HAProxy → shipper → service → audit (ENV-NFR-03, AC-12).

7. **No `golden-signals` internals.** This ADR covers the **bridge and the HAProxy log source
   configuration only**. Windowing, percentile maths, key assembly, queueing, and retention remain
   owned by SPEC-LGS-001 / ADR-0066/0067/0068/0069. The shipper is just another `/ingestion` client.

---

## Amendment 2026-06-17 (Refs #28) — runtime transport edge: HAProxy SYSLOG → shipper TCP listener

**Status of this amendment:** Accepted (human-approved fix; ADR remains **Accepted**).
**Trigger:** Phase-8 live integration surfaced **Defect B (issue #28)**. The original decision
pinned _what_ the shipper parses (the `GSLOG` log-format) and _how_ it delivers (at-least-once,
idempotent ids, `%Tr` latency) but **never specified the runtime transport edge** by which an
HAProxy access-log line physically reaches the shipper. The first implementation had the shipper
read **stdin**, on the assumption HAProxy's stdout would be piped to it — but HAProxy has **no
native file logging and the compose topology never connected the two stdio streams**, so the
shipper hit EOF immediately and restart-looped (10+ restarts, 0 output). The `HAProxy → Ingestion`
bridge (SPEC-LGS-002 §1.1) was therefore broken end-to-end.

### Decision (transport edge)

8. **Transport edge — HAProxy emits the pinned `GSLOG` access-log format via SYSLOG to a
   `gs-log-shipper` syslog listener on the internal `gs-net`.** HAProxy's native, first-class log
   sink is syslog (its `log` directive); it has no file sink. The shipper therefore **listens for
   syslog frames**, strips the syslog envelope (the RFC 3164 `<PRI>` priority + any prefix), and
   feeds the recovered `GSLOG\t…` line into the **existing parse → normalise (§9.1 epoch-ms) →
   batch → POST /ingestion → bounded-retry pipeline unchanged**. The `stdin` reader is retired.
   The listener binds **only on `gs-net`** and is **NOT host-published** (ENV-FR-12 / ADR-0085 §1);
   its bind address and port are env-configurable (`GS_SHIPPER_SYSLOG_HOST` default `0.0.0.0`,
   `GS_SHIPPER_SYSLOG_PORT` default `514`) so the listener is reachable by the `gs-haproxy`
   service name on the isolated bridge and nowhere else.

9. **TCP, not UDP (justified).** The syslog transport is **TCP** (`log tcp@gs-log-shipper:514 …`
   on the HAProxy side; a TCP stream listener on the shipper side). UDP syslog is rejected because
   UDP **silently drops** datagrams under load or buffer pressure — directly contradicting
   ENV-FR-04 ("never silently discard") and undermining the `shipper_delivery_ratio ≥ 99.9 %` SLI
   this ADR's at-least-once posture (decision 3) exists to protect. A drop on the very first hop,
   before the shipper's batching/retry machinery can see the line, is invisible loss that no
   downstream counter can attribute — strictly worse than the bounded, _counted_ drop the retry
   budget already accepts. TCP gives the shipper a connected, back-pressurable byte stream over the
   single-node `gs-net`, with the OS socket buffer providing flow control rather than silent loss.
   We accept TCP's slightly higher per-message cost as negligible for a single-node demonstration
   rig and a clear win for the delivery-ratio SLI. (No concrete repo/HAProxy reason was found to
   prefer UDP; HAProxy 2.9 supports `tcp@` log targets natively.)

   This amendment does **not** alter the `%Tr`/`%Tt` field choice (decision 2), the at-least-once +
   idempotent-batch-id posture (decision 3), the bounded-retry / `Retry-After` / 401-fail-fast /
   422-drop behaviour (decision 4), or the PII posture (decision 6) — all are preserved verbatim;
   only the _first-hop transport_ into the shipper changes from stdin to a TCP syslog listener.

### Consequences of the amendment

- **Positive:** the `HAProxy → Ingestion` bridge is now actually wired and ships end-to-end; the
  first hop is a connected, flow-controlled TCP stream consistent with the never-silently-discard
  SLI. The shipper's untrusted-input hardening now also defends the syslog frame boundary
  (oversized/malformed frame → drop + `parse_errors` counter, never crash).
- **Trade-off:** the shipper now owns a network listener (a new inbound surface), confined to
  `gs-net` and never host-published. A malformed or oversized syslog frame is treated exactly like
  a malformed access-log line — dropped and counted, never fatal.
- **Threat-model delta:** the untrusted-input boundary moves from "stdin byte stream" to "TCP
  syslog frame on `gs-net`"; the parse/oversize/never-crash guarantees that protected the stdin
  path now protect the framed path. Recorded against the existing boundary in
  `specs/security/threat-model-SPEC-LGS-001-golden-signals.md` (Refs #22, #28).

## Consequences

### Positive

- The article's implied-but-unspecified bridge is now a concrete, contract-pinned, first-party
  component with repo-governed tests and pinned dependencies.
- `%Tr`-as-latency yields a faithful server-side Golden-Signals latency, not a client-inflated one.
- At-least-once + idempotent ids means an ambiguous failure can **never** silently lose a request —
  the failure mode is a _bounded, documented over-count_, which for a Golden-Signals demonstration
  rig is strictly preferable to undercounting.
- Reuses the SPEC-LGS-001 §8 HTTP contract verbatim — no new service surface; the service stays a
  black box (ADR-0066 honoured).

### Negative / Trade-offs

- **Documented over-count under retry (the §13 risk).** Traffic counts can exceed the true count by
  a bounded amount when an ambiguous `5xx` triggers a re-delivery the service already applied. This
  is accepted and documented (AC-04 reconciled in (3) above); a strictly-exactly-once design was
  rejected as over-engineering for a demonstration rig.
- **`%Tr` vs `%Tt` semantic gap (the §13 risk).** Operators reading the rig must understand that the
  latency Golden Signal is _server_ response time (`%Tr`); `%Tt` (total, client-inclusive) is carried
  only as context and must not be mistaken for the signal. Documented in §9.1 and §10(a).
- **Bespoke-shipper maintenance cost.** First-party parse/normalise/ship code is ours to maintain and
  test, versus inheriting an off-the-shelf shipper's hardening. Mitigated by pinned deps + SBOM
  (ENV-NFR-05/08) and the abuse/parse tests authored at Phase 8.

### Neutral

- The HTTP contract, key grammar, masking, and queue are unchanged — owned upstream by
  SPEC-LGS-001 / ADR-0066/0067/0068/0069.
- Whether HAProxy fronts a trivial upstream or returns canned responses is a compose-environment
  detail recorded in ADR-0085, not here.

## Alternatives Considered

- **Off-the-shelf shipper (Vector / Fluent Bit + transform) — Q2.** Rejected: a config-DSL transform
  to hit the §9.1 schema plus a third-party agent's supply-chain surface is disproportionate for a
  demonstration rig, and moves the boundary contract out of repo-governed test coverage.
- **`%Tt` (total time) as the latency signal — Q4.** Rejected: includes client-side time and
  overstates server latency, defeating the Golden-Signals intent. `%Tt` retained as context only.
- **At-most-once with a documented loss budget — Q3.** Rejected: for a Golden-Signals rig, silently
  _under_-counting traffic/errors is worse than a bounded, idempotent-id-mitigated over-count;
  ENV-FR-04 forbids silent discard.
- **Exactly-once delivery.** Rejected: requires service-side dedup state and distributed-transaction
  machinery unjustified for a single-node demonstration environment; idempotent batch ids give the
  pragmatic bound instead.

## Compliance & Risk

- **Controls affected:** adds a new untrusted-input boundary (HAProxy line → shipper) and a new
  authenticated client of `/ingestion`. The STRIDE delta for these boundaries is recorded in
  `specs/security/threat-model-SPEC-LGS-001-golden-signals.md` (Refs #22), not restated here.
- **Data classification impact:** `client_ip` is telemetry-L2 / PII; masked by the service before
  persist/log (ADR-0012); shipper must not log it raw (ENV-NFR-04). DPIA lightweight-register note
  recorded as Activity 6 (full DPIA waived per Phase-2 human disposition; SPEC-LGS-002 §11).
- **Autonomy impact:** none — the shipper performs no autonomous outward action; it surfaces but
  never acts on the service's HITL flip (ADR-0011).
- **Review/expiry:** scoped to SPEC-LGS-002; revisit if the rig is promoted toward production
  throughput (the Kafka scale-out exit recorded in ADR-0069 applies to the service, not this bridge).

---

## Related

- `docs/adr/README.md` — master index & lifecycle definition
- `docs/adr/adr-review-checklist.md` — checklist to apply before marking this ADR `Accepted`
- [SPEC-LGS-002](../../specs/infrastructure/SPEC-LGS-002-golden-signals-environment.md) — §7/§8/§9.1/§13/§15-Q2/Q3/Q4
- [ADR-0085](ADR-0085-golden-signals-compose-environment.md) — the compose environment that runs this bridge
- [ADR-0086](ADR-0086-demonstration-traffic-generator.md) — the generator that drives the HAProxy log source
- `specs/security/threat-model-SPEC-LGS-001-golden-signals.md` — STRIDE delta for the new boundaries (Refs #22)
