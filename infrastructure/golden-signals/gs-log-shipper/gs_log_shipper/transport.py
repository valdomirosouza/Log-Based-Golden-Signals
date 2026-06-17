"""httpx-backed Transport for gs-log-shipper.

Kept separate from :mod:`shipper` so the core batching/retry logic stays
network-free and unit-testable (the tests inject a fake Transport). This module
holds the only real outbound-HTTP boundary in the component.

Refs: SPEC-LGS-002 §8, ADR-0084. Issue #24 (B-10).
"""

from __future__ import annotations

from collections.abc import Sequence

import httpx

from .shipper import IngestResult


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header (delta-seconds form) to float seconds."""
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


class HttpxTransport:
    """Real HTTP transport over httpx with a shared client."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client()

    def post_ingestion(
        self,
        url: str,
        payload: Sequence[dict[str, object]],
        headers: dict[str, str],
        timeout: float,
    ) -> IngestResult:
        """POST the JSON batch and map the response to an :class:`IngestResult`."""
        try:
            response = self._client.post(url, json=list(payload), headers=headers, timeout=timeout)
        except httpx.TimeoutException:
            # Treat a timeout like a retryable 503 (ambiguous outcome).
            return IngestResult(status_code=503)
        except httpx.HTTPError:
            return IngestResult(status_code=503)

        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        accepted = 0
        rejected = 0
        if response.status_code == 202:
            try:
                body = response.json()
                accepted = int(body.get("accepted", 0))
                rejected = int(body.get("rejected", 0))
            except (ValueError, AttributeError, TypeError):
                # 202 with an unparsable body still counts as delivered.
                accepted = len(payload)
        return IngestResult(
            status_code=response.status_code,
            accepted=accepted,
            rejected=rejected,
            retry_after_seconds=retry_after,
        )
