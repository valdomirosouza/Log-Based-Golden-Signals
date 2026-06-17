# golden-signals STUB — contract-shaped responder until the real Java image
# exists (ADR-0066 carry-over). Stdlib-only, no third-party deps. Pinned base
# (ENV-NFR-01: no :latest). Refs SPEC-LGS-002 §13/§15-Q1, ADR-0085 §8, issue #26.
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

RUN useradd --create-home --uid 10003 stub
WORKDIR /app
COPY stub.py /app/stub.py
USER stub

EXPOSE 8085
ENTRYPOINT ["python", "/app/stub.py"]
