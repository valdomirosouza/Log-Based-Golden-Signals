"""gs-log-shipper — HAProxy access-log → POST /ingestion bridge.

SPEC-LGS-002 (ENV-FR-03/04), ADR-0084 (bespoke Python forwarder;
at-least-once with idempotent batch ids; %Tr latency signal, %Tt context;
bounded retry honouring Retry-After). Refs #18, #24 (B-10).

This package is *environment scaffolding* (ADR-0085 §7) — it is not a
first-class service and has no services.yaml entry. It is just another client
of the SPEC-LGS-001 §8 `/ingestion` contract.
"""

__all__ = ["config", "logline", "shipper"]
