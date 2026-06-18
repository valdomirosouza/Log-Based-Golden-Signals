"""gs-traffic-generator — deterministic synthetic-load driver for the rig.

SPEC-LGS-002 (ENV-FR-07), ADR-0086 (deterministic/seeded; >=5 paths; scenarios
steady|latency-burst|error-burst). Drives HTTP load at the HAProxy listener
only (never directly at the shipper or service) so HAProxy emits genuine
access-log lines. Synthetic test data only — no real PII (CLAUDE.md §3.1).
Refs #18, #25 (B-11). Environment scaffolding, not a services.yaml entry
(ADR-0085 §7).
"""

__all__ = ["config", "plan"]
