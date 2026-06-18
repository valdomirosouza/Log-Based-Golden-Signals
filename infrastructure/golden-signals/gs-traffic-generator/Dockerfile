# gs-traffic-generator — deterministic synthetic-load driver (ADR-0086).
# Pinned base image (ENV-NFR-01: no :latest) for reproducible builds and a
# fixed Trivy/SBOM scan surface (ENV-NFR-05). Refs SPEC-LGS-002, issue #25.
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

RUN useradd --create-home --uid 10002 generator
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY gs_traffic_generator/ /app/gs_traffic_generator/

USER generator

ENTRYPOINT ["python", "-m", "gs_traffic_generator.main"]
