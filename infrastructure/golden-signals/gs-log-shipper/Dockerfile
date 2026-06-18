# gs-log-shipper — HAProxy access-log → POST /ingestion bridge (ADR-0084).
# Pinned base image (ENV-NFR-01: no :latest) for reproducible builds and a
# fixed Trivy/SBOM scan surface (ENV-NFR-05). Refs SPEC-LGS-002, issue #24.
FROM python:3.13.7-slim-bookworm

# Phase-9 CVE remediation (Refs #29, #18; ADR-0029 "zero fixable CRITICAL ships").
# Apply outstanding Debian security patches early so fixable base-image CRITICALs
# (libssl3/openssl CVE-2026-31789, libgnutls30 CVE-2026-33845/CVE-2026-42010) are
# cleared at build time. Residual genuinely-unfixable CRITICALs (zlib1g/perl-base/
# libsqlite3-0 — will_not_fix/deferred) are risk-accepted in
# infrastructure/golden-signals/SECURITY-CVE-ACCEPTANCE.md. Base stays pinned
# (ENV-NFR-01); no base-family switch.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime (least privilege; the shipper needs no elevated rights).
RUN useradd --create-home --uid 10001 shipper
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the importable package; `python -m gs_log_shipper.main` then resolves
# from /app. Tests are not copied into the runtime image.
COPY gs_log_shipper/ /app/gs_log_shipper/

USER shipper

# Runtime transport edge (ADR-0084 Amendment 2026-06-17 / Defect B, issue #28):
# the shipper runs a TCP syslog listener (default :514 on gs-net, NOT host-
# published) that HAProxy logs to via `log tcp@gs-log-shipper:514`. The
# entrypoint serves that listener; it no longer reads stdin.
EXPOSE 514
ENTRYPOINT ["python", "-m", "gs_log_shipper.main"]
