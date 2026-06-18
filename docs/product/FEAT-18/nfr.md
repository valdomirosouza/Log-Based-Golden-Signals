# Non-Functional Requirements & PII Classification — FEAT-18

> **⚡ Agent-Generated:** This document was drafted by Claude Code on 2026-06-16.
> **Human Review Required:** Security Lead (mandatory, blocking) + Tech Lead must review and approve before this artefact is actioned.
> **Review Status:** Draft
> **Reviewer:** _(pending — Security Lead + Tech Lead)_ | **Approved:** _(pending)_

| Field         | Value                                                                                                                                                                                          |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Feature ID    | FEAT-18 ([Issue #18](https://github.com/valdomirosouza/Log-Based-Golden-Signals/issues/18))                                                                                                    |
| Spec          | `SPEC-LGS-002-golden-signals-environment.md` §6 (NFR), §11 (Governance/Privacy/Security)                                                                                                       |
| Phase         | 2 — Discovery (CONTROL phase; `processes_data` = TRUE)                                                                                                                                         |
| Gate          | `phase-gates.yaml` id=2 — required artefact; approvals **`security_lead` + `tech_lead`**; exit "NFR doc with PII classification; Security Lead approved"; **blocking** for Definition of Ready |
| Taxonomy      | `docs/product/nfr-taxonomy.md` (categories used verbatim from its core table)                                                                                                                  |
| PII reference | `docs/privacy/pii-inventory.md` · ADR-0012                                                                                                                                                     |

> **Taxonomy rule (enforced below):** every NFR maps to **evidence / a gate**. An NFR with no
> instrument is a wish, not a requirement. Categories are taken from `docs/product/nfr-taxonomy.md`
> core table; each is grounded — no fabricated category.

---

## 1. NFR Classification (spec §6 ENV-NFR-01..08)

| ID             | Requirement (measurable budget)                                                                                                                                                                                                                                                                                                    | Taxonomy category              | Evidence / gate it maps to                                                                                                                                                                         |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **ENV-NFR-01** | **Containerisation / reproducibility.** All components are Docker Compose containers under the `golden-signals` profile; **pinned base-image tags** (no `latest`); a clean checkout reproduces the environment bit-for-bit.                                                                                                        | **Operability**                | Compose file (`docker-compose.yml` profile + `infrastructure/golden-signals/`); AC-01 (`make gs-up` → all-healthy).                                                                                |
| **ENV-NFR-02** | **Config-via-env.** 100% of tunables via env vars with defaults in `.env.example`; no hard-coded hosts/ports/keys in images; starts from defaults alone except values marked _(required)_ (e.g. `GS_API_KEYS`).                                                                                                                    | **Operability**                | `.env.example` diff; AC-11 (defaults + `GS_API_KEYS` → healthy, no committed secret).                                                                                                              |
| **ENV-NFR-03** | **Structured logging + trace propagation.** Shipper & generator emit structured JSON logs; shipper sets/propagates `X-Trace-Id` on each `POST /ingestion` so a request is traceable HAProxy→shipper→service (SPEC-LGS-001 NFR-03).                                                                                                 | **Observability**              | Log sample; AC-12 (same `X-Trace-Id` in shipper log + service audit entry); `skills/observability/otel-instrumentation.md`.                                                                        |
| **ENV-NFR-04** | **PII safety in transit.** `client_ip` masked **before persistence/logging** by the service (SPEC-LGS-001 FR-02); environment SHALL NOT expose raw HAProxy logs outside the internal network; shipper SHALL NOT write raw client IP to its own stdout. **Shipped payload classified telemetry-L2 with a PII field (`client_ip`).** | **Privacy**                    | PII classification (§2 below); `docs/privacy/pii-inventory.md`; AC-06 (no unmasked IP octet/hextet in Redis dump, shipper stdout, container logs) + PiiLeak scan; maps ADR-0012, `specs/privacy/`. |
| **ENV-NFR-05** | **Image supply-chain.** Every image built/pulled is Trivy-scanned and an SBOM produced in CI; **no `CRITICAL` unfixed vuln ships** (ADR-0029).                                                                                                                                                                                     | **Security** (DevSecOps)       | CI scan log + SBOM artifact; `make sbom`; Phase 9 gate (`trivy`, `sbom`); `skills/devsecops/pipeline-security.md`.                                                                                 |
| **ENV-NFR-06** | **Resilience under load.** Shipper handles ingestion backpressure (ENV-FR-04 — bounded retry/backoff honouring `Retry-After`) without crashing; environment survives a Redis restart with bounded, documented data loss (in-flight aggregates only).                                                                               | **Reliability**                | AC-07 (429/`Retry-After` backoff, recoverable portion dropped = 0) + AC-08 (Redis restart recovery).                                                                                               |
| **ENV-NFR-07** | **Cost envelope.** Aggregate CPU/memory ceiling for the whole profile documented and enforced (per-container limits, ENV-FR-11); fits a developer laptop / CI runner.                                                                                                                                                              | **Cost (FinOps)**              | AC-14 (every container reports non-zero CPU/mem limit; aggregate ceiling not exceeded under `steady`); maps ADR-0020; `specs/sre/finops.md`.                                                       |
| **ENV-NFR-08** | **Pinned dependencies + manifest.** Shipper/generator dependencies pinned (no version ranges); `dependency-manifest.yaml` + SBOM produced.                                                                                                                                                                                         | **Portability** (supply-chain) | Dependency manifest + lockfile; SBOM artifact; `make sbom`; ADR-0029.                                                                                                                              |

> **Note on category grounding.** Categories `Operability`, `Observability`, `Privacy`, `Security`,
> `Reliability`, `Cost (FinOps)`, and `Portability` are all present in the `docs/product/nfr-taxonomy.md`
> core table (verified). The spec §6 inline labels write "Security/DevSecOps" (→ **Security**) and
> "Supply-chain" for ENV-NFR-08; the closest grounded taxonomy category is **Portability** ("pinned
> deps + SBOM, ADR-0029" is that row's named evidence). There is **no standalone "Supply-chain"
> category** in the taxonomy — flagged so the reviewer can confirm the mapping. _(uncertain — verify
> at NFR review: confirm ENV-NFR-08 → Portability vs adding a Supply-chain category to the taxonomy.)_

---

## 2. PII Classification (CONTROL — the gate's core obligation)

This feature introduces a **new PII-processing path**: HAProxy captures the client source IP (`%ci`)
in its access log; the bespoke `gs-log-shipper` reads that line, places `client_ip` into the canonical
log entry (spec §9.1), and transmits it to the `golden-signals` service over the internal network.

| PII field   | Source                                                                  | Classification                                             | Inventory match                                                                    | Masking token | Masking boundary / rule                                                                                                                                                                                                                                                                                                            |
| ----------- | ----------------------------------------------------------------------- | ---------------------------------------------------------- | ---------------------------------------------------------------------------------- | ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `client_ip` | HAProxy `%ci` → canonical entry `client_ip` → `POST /ingestion` payload | **L2 — Sensitive (PII)** ("telemetry-L2 with a PII field") | `request.ip_address` (L2) in `docs/privacy/pii-inventory.md`; 30-day log retention | `[IP]`        | **Masked by the service before persist/log** (SPEC-LGS-001 FR-02; ADR-0012 interception points). The environment additionally: (a) confines raw HAProxy logs to the **internal bridge network** (ENV-FR-12 — Redis & shipper not host-published); (b) **forbids the shipper from writing raw `client_ip` to stdout** (ENV-NFR-04). |

**Why L2 (grounded, not assumed):** `docs/privacy/pii-inventory.md` §Classification Scheme lists
"IP address" as **L2 (Sensitive — data that identifies a person)** with masking rule "Mask in all log
streams. Pseudonymise for analytics. Token `[IP]`," and the field inventory row `request.ip_address`
= **L2**, token `[IP]`, retention "30 days (log retention)." The spec's label "telemetry-L2" is
consistent with this. No L1 fields are introduced; no L3/L4 fields are added by this feature.

**Synthetic-data standard for tests** (per inventory): client IPs in fixtures/tests MUST use
`192.0.2.x` (TEST-NET, RFC 5737) — never real IPs (CLAUDE.md §3.1; this `nfr.md` itself contains no
real PII).

### 2.1 STRIDE — Information-Disclosure boundary (spec §11)

Raw `client_ip` must not leak via: shipper stdout (forbidden, ENV-NFR-04), host-published ports
(Redis is **not** host-published, ENV-FR-12), or unmasked store contents (service masks before
persist; verified by AC-06 PiiLeak scan over Redis dump + all container logs). These are the
environment-level controls; the masking _implementation_ itself belongs to SPEC-LGS-001 / ADR-0012
and is out of scope here (the environment verifies it holds end-to-end).

---

## 3. DPIA / RIPD — REQUIRED (HUMAN GATE — NOT self-approved)

**This feature adds a new PII-processing path (client IP transmitted through a new `gs-log-shipper`
component).** Per CLAUDE.md §3.1 ("Any new PII processing requires DPIA/RIPD review") and the
`docs/privacy/pii-inventory.md` pre-release checklist ("DPIA/RIPD reviewed if this field changes the
processing activity scope"; "DPO notified for any new L1 or L2 field"), a **DPIA/RIPD review is
required** before this feature may enter a sprint / be implemented.

This is surfaced as a **governance/HITL item for human decision**. The agent does **NOT** self-approve
it. See `docs/privacy/dpia/` and `docs/privacy/ripd/` for the review templates and
`specs/privacy/dpia-ripd.md` for the process.

| Governance item                                            | Owner / role                  | Status              | Blocking                           |
| ---------------------------------------------------------- | ----------------------------- | ------------------- | ---------------------------------- |
| DPIA / RIPD review for the new `client_ip` processing path | DPO (notified)                | **Open — required** | Yes — before implementation        |
| `nfr.md` approval (PII classification security gate)       | **Security Lead (mandatory)** | **Open**            | Yes — blocks Definition of Ready   |
| `nfr.md` co-review                                         | Tech Lead                     | **Open**            | Yes (phase-gate required approval) |
| Add `client_ip`-via-shipper to data-processing register    | DPO                           | **Open**            | Pre-implementation                 |

> **Note.** `request.ip_address` already exists in the PII inventory (API gateway source). This
> feature does **not** add a brand-new _field type_, but it **does** introduce a new _processing
> activity / data flow_ (HAProxy → bespoke shipper → service). Under §3.1 and the inventory checklist
> that change of processing scope is the DPIA/RIPD trigger. _(Reviewer to confirm whether a new
> register entry or an update to the existing `request.ip_address` entry is the correct record.)_

---

## 4. Gate outcome (Phase 2)

- Required artefact `docs/product/FEAT-18/nfr.md`: **drafted, ready for review.**
- PII classification: **present** (`client_ip` = L2, §2).
- Required approvals **`security_lead` + `tech_lead`**: **NOT given** — human gate.
- DPIA/RIPD: **required, open** — human/DPO gate.

**Gate = BLOCKED** pending the human approvals + DPIA/RIPD review above. The agent cannot self-approve
a CONTROL-phase security gate (CLAUDE.md §3.1; `docs/product/README.md` §Governance Rules 2).

---

## 5. References

- `SPEC-LGS-002-golden-signals-environment.md` §6, §9.1, §11
- `docs/product/nfr-taxonomy.md` (categories)
- `docs/privacy/pii-inventory.md` (`request.ip_address` L2 / `[IP]`; synthetic-data standard; pre-release checklist)
- `docs/adr/ADR-0012-pii-masking-strategy.md` (interception-point masking)
- `docs/process/gates/phase-gates.yaml` id=2 (gate contract)
- `docs/product/FEAT-18/discovery.md` (companion Discovery artefact)
