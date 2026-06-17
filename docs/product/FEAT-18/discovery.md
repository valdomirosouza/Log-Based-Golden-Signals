# Discovery — FEAT-18: Log-Based Golden Signals Containerised Runtime Environment

> **⚡ Agent-Generated:** This document was drafted by Claude Code on 2026-06-16.
> **Human Review Required:** Product Owner + Tech Lead must review and approve before this artefact is actioned.
> **Review Status:** Draft
> **Reviewer:** _(pending)_ | **Approved:** _(pending)_

| Field              | Value                                                                                                                                                                   |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Feature ID         | FEAT-18 (= GitHub Issue [#18](https://github.com/valdomirosouza/Log-Based-Golden-Signals/issues/18); per `docs/product/README.md` `{id}` = parent feature Issue number) |
| Spec               | `SPEC-LGS-002-golden-signals-environment.md` (status: **draft**; canonical target `specs/infrastructure/`, moves at Phase 4)                                            |
| Backlog            | B-02 (`reports/SPEC-LGS-002-golden-signals-environment/backlog.yaml`)                                                                                                   |
| Phase              | 2 — Discovery (CONTROL phase: `control_phase: true`, condition `processes_data` = TRUE)                                                                                 |
| Tier               | GOVERNED (REGULATED escalation pre-authorised at Phase 0)                                                                                                               |
| Governing ADRs     | ADR-0012 (PII masking — client IP), ADR-0058 (Agentic SDLC); spec also reuses ADR-0003/0011/0020/0026/0029/0066/0067/0068/0069                                          |
| Companion artefact | `docs/product/FEAT-18/nfr.md` (Phase 2 NFR + PII classification — **Security Lead approval mandatory**)                                                                 |

---

## 1. Context

`SPEC-LGS-001` specifies the four-component Log-Based Golden Signals pipeline (Ingestion API →
in-JVM bounded queue → Metrics Processor → Redis store → Analytics API) and its Java 21 / Spring
Boot implementation (ADR-0066). A _specified_ application is not a _running_ pipeline. The source
article (Souza Jr., PPGCA/Unisinos 2026, §3.4) names the runtime stack — HAProxy, Redis, Docker,
Docker Compose — but two pieces remain unspecified and block any real end-to-end run:

1. **No defined bridge from HAProxy to the Ingestion API.** HAProxy emits syslog / access-log
   lines; the Ingestion API consumes JSON batches over `POST /ingestion` (SPEC-LGS-001 §8). No
   component performs the parse → normalise → batch → HTTP-POST translation.
2. **No reproducible, governed orchestration** that stands the log source, bridge, service and
   store up together, in health order, on an isolated network, with the demonstration data needed
   to exercise the SPEC-LGS-001 acceptance criteria at the environment level.

SPEC-LGS-002 owns the **environment** (orchestration + log source + log-shipping bridge + the
demonstrable end-to-end run). It does **not** re-specify the application's internal logic; where the
two meet it references SPEC-LGS-001 by FR/AC id (spec §SCOPE BOUNDARY).

## 2. Problem statement

> How can the SPEC-LGS-001 pipeline be stood up as a single-command, reproducible, governed
> environment — fed by a real HAProxy log source through an explicit log-shipping bridge — so the
> four Golden Signals are demonstrably computed from genuine proxy traffic, and the SPEC-LGS-001
> acceptance criteria become runnable evidence rather than assertions?

Cost of not solving it: SPEC-LGS-001 stays a paper design; the dissertation MTTD/MTTR demonstration
cannot be exercised, measured, or shown; every reviewer must hand-assemble a non-reproducible rig.

## 3. New components in scope

| Component              | Nature                                                                                                                | Language                                          | Notes                                                                                  |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `gs-log-shipper`       | **[NEW]** bridge: HAProxy access-log line → canonical entry → batch → `POST /ingestion` (retry/backoff, ENV-FR-03/04) | **Python** (bespoke forwarder — Phase-0 decision) | Off-the-shelf vs bespoke is open Q-2; Phase-0 fixed it to **bespoke Python forwarder** |
| `gs-traffic-generator` | **[NEW]** deterministic synthetic HTTP load (≥5 paths; steady / latency-burst / error-burst scenarios)                | **Python**                                        | Drives HAProxy so it produces genuine logs                                             |
| `haproxy`              | reused, configured as the **log source** with a pinned `log-format` (ADR-0068 fields)                                 | config-only                                       | Real proxy in front of a trivial upstream / canned responses                           |
| `golden-signals`       | reused **black box** honouring SPEC-LGS-001 §8 HTTP contract                                                          | Java 21 / Spring Boot (ADR-0066)                  | Environment is language-neutral to it                                                  |
| `redis`                | reused single-node store (ADR-0067)                                                                                   | n/a                                               | Internal network only, password-protected, ephemeral by default                        |

## 4. Consumers & Personas (spec §4)

| Consumer                                     | Need from this environment                                                                                                     |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| Researcher / dissertation reviewer (primary) | Reproducible rig demonstrating Golden Signals from real HAProxy traffic on one command                                         |
| `/deliver` CODE mode (SPEC-LGS-001)          | A runnable target to validate the service's ACs (AC-01/04/05/08/10) against                                                    |
| SRE / NOC engineer                           | Local replica of the ingestion path to reproduce incident-time `/analytics` behaviour and tune HITL thresholds                 |
| Future Agentic Copilot layer                 | A live `GET /analytics` endpoint exposing governed percentiles (out of scope here; merely exposed)                             |
| Platform / governance owner                  | Evidence that PII masking, network isolation, image scanning, cost limits and audit controls hold in the assembled environment |

## 5. Constraints

- **Binding-ADR constraint.** The service image is built per **ADR-0066 (Java 21 / Spring Boot)**.
  The article specifies Python/FastAPI + Celery/RQ; this environment is deliberately language-neutral
  and treats the service as a §8 black box (open Q-1).
- **Phase-0 human decisions (honoured):** (a) mature DRAFT spec through Phases 1–5 to _approved_
  before implementing; (b) `gs-log-shipper` = bespoke Python forwarder; (c) **no runnable
  golden-signals Java image yet** → environment + tests are written against the SPEC-LGS-001 §8
  contract via stub/mock; the live `make gs-demo` run (AC-01 / AC-10) is **deferred and logged as a
  gap**; (d) GOVERNED→REGULATED escalation pre-authorised.
- **Demonstration ≠ production.** Local / CI Docker Compose only (production cloud is `SPEC-INFRA-001`);
  single-node Redis, ephemeral persistence, laptop-scale resource limits.
- **No committed secrets** (`GS_API_KEYS` etc. via env only, ENV-FR-09); per-container CPU/mem limits
  (ADR-0020); surgical teardown leaving zero residue (ENV-FR-10).
- **Privacy constraint (CONTROL).** The shipped payload carries `client_ip` (L2 PII). Masking is the
  service's responsibility (SPEC-LGS-001 FR-02); the environment must additionally confine raw logs
  to the internal network and forbid the shipper from writing raw IP to stdout (ENV-NFR-04). See
  `nfr.md`.

## 6. Assumptions (to validate, not assume silently)

- A-1: SPEC-LGS-001 §8 HTTP contract (`POST /ingestion`, `GET /analytics`, `GET /analytics/health`)
  is stable enough to code the shipper and tests against via stub/mock. _(Grounded in spec §8;
  live image deferred per Phase-0.)_
- A-2: `golden-signals` is registered in `services.yaml` (Java, port 8085) and `redis` already
  exists in `docker-compose.yml` (spec §1.3). **Verify at the architecture/development gate before
  editing compose.**
- A-3: HAProxy `%Tt`/`%Tr`/`%Ta` fields and `%ci`/`%ST`/`%B`/`%b` captures are available in the
  pinned HAProxy version (ADR-0068 field set). _(Open Q-4 chooses the timing field.)_
- A-4: `client_ip` = `request.ip_address` in `docs/privacy/pii-inventory.md` (L2, token `[IP]`,
  30-day log retention). **Grounded — confirmed present in the inventory.**

## 7. Discovery risks & decisions-needed (spec §15 open questions surfaced)

These are the six spec §15 open questions, surfaced as discovery items. They are **decisions for
HITL gates** (architecture / development), not assumptions to resolve here. `/deliver` lists them
as open-HITL items.

| #   | Open question (spec §15)                                                                                                                     | Type            | Resolves at                                 | Discovery note                                                                                                          |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------- | --------------- | ------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Q-1 | **Article-vs-ADR-0066 runtime.** Keep Java image (ADR-0066, recommended) or open a superseding ADR to revert to Python to match the article? | Decision-needed | Phase 5 Architecture / governance           | Affects only which prebuilt image the compose profile references. Phase-0 leans **keep ADR-0066** (honour binding ADR). |
| Q-2 | **Shipper implementation** — off-the-shelf (Vector / Fluent Bit + transform) vs bespoke forwarder?                                           | Decision-needed | `haproxy-log-shipping-bridge` ADR (Phase 5) | **Phase-0 fixed: bespoke Python forwarder.** Supply-chain surface (ENV-NFR-05/08) vs maintenance trade-off recorded.    |
| Q-3 | **Delivery semantics** — at-least-once + idempotent batch ids vs at-most-once with documented loss budget?                                   | Risk + decision | Bridge ADR (Phase 5)                        | Drives whether traffic counts are exact (ENV-FR-04, AC-04). At-least-once without idempotency risks double-count.       |
| Q-4 | **HAProxy timing field** — `%Tt` (total) vs `%Tr`/`%Ta` (response/active) as `response_time_ms`?                                             | Decision-needed | Bridge ADR (Phase 5)                        | `%Tt` includes client-side time and can overstate server latency vs article intent (spec §13).                          |
| Q-5 | **Generator/shipper as `services.yaml` services?** Register, or stay environment scaffolding under `infrastructure/golden-signals/`?         | Decision-needed | Architecture gate (Phase 5)                 | **Default: scaffolding** (spec §14); confirm at architecture gate.                                                      |
| Q-6 | **Redis persistence in the rig** — ephemeral (clean `gs-down`) vs `--save` (replayable for AC-08)?                                           | Decision-needed | Compose ADR (Phase 5)                       | **Default: ephemeral**; AC-08 tested via explicit restart, not persistence.                                             |

### Additional discovery risks (spec §13)

- **R-A: HAProxy log fidelity** — `%Tt` total time can overstate server-side latency (ties to Q-4).
- **R-B: At-least-once vs exact counts** — re-delivery on ambiguous `5xx` could double-count (ties to Q-3).
- **R-C: Synthetic traffic realism** — a deterministic generator demonstrates the _mechanism_, not
  field-representative percentile numbers.
- **R-D: Live demonstration gap (Phase-0)** — no runnable Java image yet ⇒ AC-01 / AC-10 live
  `make gs-demo` is **deferred**; environment + tests validate against the §8 contract via stub/mock.
  Tracked as an explicit gap, not silently passed.
- **R-E: Scope ceiling** — GOVERNED caps at 25 files / 3 ADRs; this feature plausibly exceeds it
  (compose + HAProxy cfg + shipper + generator + Makefile + `.env.example` + SLO yaml + runbook +
  3 ADRs + discovery + nfr + CHANGELOG + CODEOWNERS + spec move). Phase-0 **pre-authorised the
  GOVERNED→REGULATED escalation**; this Discovery phase's CONTROL/PII surface independently warrants
  it (see `nfr.md` and the TIER_ESCALATION below).

## 8. Privacy / control surface (summary — full classification in `nfr.md`)

This feature introduces a **new PII-processing path**: HAProxy `%ci` (`client_ip`) flows through the
bespoke `gs-log-shipper` to the service. `client_ip` = **L2 PII** (`request.ip_address`,
`docs/privacy/pii-inventory.md`; token `[IP]`; ADR-0012). Masking is the service's responsibility
(SPEC-LGS-001 FR-02); the environment confines raw logs to the internal network and forbids raw IP
on shipper stdout (ENV-NFR-04). **A new PII-processing path ⇒ DPIA/RIPD review is required**
(CLAUDE.md §3.1) — surfaced as a HITL/governance item in `nfr.md`; **not self-approved here.**

## 9. Exit gate (Phase 2)

Per `docs/process/gates/phase-gates.yaml` id=2: required artefacts `nfr.md` (+ Phase-1 `discovery.md`);
required approvals **`security_lead` + `tech_lead`**; exit criteria "NFR doc + PII classification;
Security Lead approved". This phase **blocks Definition of Ready**. Drafts are ready-for-review;
human approval (Security Lead + Tech Lead) and the DPIA/RIPD flag are the STOP point.

## 10. References

- `SPEC-LGS-002-golden-signals-environment.md` §1–§15 (this feature)
- `docs/product/README.md` (FEAT-{id} = Issue number; nfr.md = security gate)
- `docs/product/nfr-taxonomy.md` (NFR category reference)
- `docs/privacy/pii-inventory.md` (`request.ip_address` L2, token `[IP]`)
- `docs/adr/ADR-0012-pii-masking-strategy.md`, `docs/adr/ADR-0058-agentic-spec-driven-delivery-workflow.md`
- `docs/process/gates/phase-gates.yaml` (id=2 gate contract)
