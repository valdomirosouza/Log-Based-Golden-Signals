"""Unit tests for AuditLogger, InMemoryAuditStorage, and PostgresAuditStorage.

Spec: specs/ai/guardrails.md (Layer 4 — Audit Logger)
ADR:  ADR-0011 (HITL/HOTL Human Oversight Model), ADR-0018 (DB Encryption at Rest)
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.guardrails.audit_logger import (
    AuditLogger,
    AuditWriteError,
    InMemoryAuditStorage,
    PostgresAuditStorage,
)
from src.shared.db_encryption import EncryptedField
from src.shared.models import AuditEvent

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_event(
    agent_id: str = "agent-test",
    action: str = "test.action.proposed",
    offset_seconds: int = 0,
) -> AuditEvent:
    return AuditEvent(
        event_type="test.event",
        agent_id=agent_id,
        action=action,
        outcome="PENDING",
        created_at=datetime.now(UTC) + timedelta(seconds=offset_seconds),
    )


# ── InMemoryAuditStorage ──────────────────────────────────────────────────────


class TestInMemoryAuditStorage:
    @pytest.mark.asyncio
    async def test_append_and_query_no_filter(self):
        storage = InMemoryAuditStorage()
        for _ in range(3):
            await storage.append(_make_event())

        results = await storage.query(
            agent_id=None,
            action_type=None,
            from_time=None,
            to_time=None,
            limit=100,
        )
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_query_filter_by_agent_id(self):
        storage = InMemoryAuditStorage()
        await storage.append(_make_event(agent_id="agent-alpha"))
        await storage.append(_make_event(agent_id="agent-alpha"))
        await storage.append(_make_event(agent_id="agent-beta"))

        results = await storage.query(agent_id="agent-alpha")
        assert len(results) == 2
        assert all(e.agent_id == "agent-alpha" for e in results)

    @pytest.mark.asyncio
    async def test_query_filter_by_action_type(self):
        storage = InMemoryAuditStorage()
        await storage.append(_make_event(action="action.read"))
        await storage.append(_make_event(action="action.write"))
        await storage.append(_make_event(action="action.read"))

        results = await storage.query(action_type="action.read")
        assert len(results) == 2
        assert all(e.action == "action.read" for e in results)

    @pytest.mark.asyncio
    async def test_query_filter_by_from_time(self):
        storage = InMemoryAuditStorage()
        cutoff = datetime.now(UTC)
        await storage.append(_make_event(offset_seconds=-120))  # before cutoff
        await storage.append(_make_event(offset_seconds=10))  # after cutoff
        await storage.append(_make_event(offset_seconds=20))  # after cutoff

        results = await storage.query(from_time=cutoff)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_query_filter_by_to_time(self):
        storage = InMemoryAuditStorage()
        cutoff = datetime.now(UTC)
        await storage.append(_make_event(offset_seconds=-20))  # before cutoff
        await storage.append(_make_event(offset_seconds=-10))  # before cutoff
        await storage.append(_make_event(offset_seconds=120))  # after cutoff

        results = await storage.query(to_time=cutoff)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_query_limit_returns_most_recent(self):
        storage = InMemoryAuditStorage()
        for i in range(5):
            await storage.append(_make_event(agent_id=f"agent-{i}"))

        results = await storage.query(limit=2)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_append_stores_a_copy(self):
        storage = InMemoryAuditStorage()
        event = _make_event()
        await storage.append(event)

        # Mutating the original does not affect the stored record
        object.__setattr__(event, "outcome", "MUTATED")
        results = await storage.query()
        assert results[0].outcome == "PENDING"


# ── AuditLogger ───────────────────────────────────────────────────────────────


class TestAuditLogger:
    @pytest.mark.asyncio
    async def test_log_event_returns_event_id(self):
        logger = AuditLogger(InMemoryAuditStorage())
        event_id = await logger.log_event(_make_event())
        uuid.UUID(event_id)  # raises ValueError if not a valid UUID

    @pytest.mark.asyncio
    async def test_audit_write_error_raised_on_storage_failure(self):
        failing_storage = InMemoryAuditStorage()
        failing_storage.append = AsyncMock(side_effect=RuntimeError("disk full"))

        logger = AuditLogger(failing_storage)
        with pytest.raises(AuditWriteError, match="disk full"):
            await logger.log_event(_make_event())


# ── PostgresAuditStorage — metadata encryption (ADR-0018) ────────────────────

_TEST_KEY = "a" * 64  # 32-byte AES-256 key as 64 hex chars


def _make_mock_pool(execute_return=None, fetch_return=None):
    """Return a mock asyncpg pool whose acquire() works as an async context manager."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=execute_return)
    conn.fetch = AsyncMock(return_value=fetch_return or [])

    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


class TestPostgresAuditStorageEncryption:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_append_encrypts_metadata_when_encryption_set(self):
        """metadata column is written as enc:v1:... ciphertext, never as plaintext JSON."""
        pool, conn = _make_mock_pool()
        enc = EncryptedField(_TEST_KEY)
        storage = PostgresAuditStorage(pool=pool, encryption=enc)

        event = _make_event()
        object.__setattr__(event, "metadata", {"user_id": "u-123", "action_params": "sensitive"})
        await storage.append(event)

        _, written_metadata = _extract_call_arg(conn.execute, pos=8)
        assert EncryptedField.is_encrypted(written_metadata), (
            "metadata must be AES-256-GCM encrypted (enc:v1: prefix) before INSERT"
        )
        # Confirm plaintext JSON is NOT present in the stored value
        assert "u-123" not in written_metadata

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_append_stores_plaintext_when_no_encryption(self):
        """Without encryption the store writes plain JSON (local dev only)."""
        pool, conn = _make_mock_pool()
        storage = PostgresAuditStorage(pool=pool, encryption=None)

        event = _make_event()
        object.__setattr__(event, "metadata", {"key": "value"})
        await storage.append(event)

        _, written_metadata = _extract_call_arg(conn.execute, pos=8)
        assert written_metadata == json.dumps({"key": "value"})

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_query_decrypts_metadata_on_read(self):
        """Rows fetched from DB are transparently decrypted back to dict."""
        enc = EncryptedField(_TEST_KEY)
        original_metadata = {"risk": "high", "target": "resource-42"}
        encrypted_value = enc.encrypt(json.dumps(original_metadata))

        fake_row = _make_fake_row(metadata=encrypted_value)
        pool, _ = _make_mock_pool(fetch_return=[fake_row])
        storage = PostgresAuditStorage(pool=pool, encryption=enc)

        results = await storage.query()
        assert results[0].metadata == original_metadata

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_query_passthrough_for_pre_existing_plaintext_rows(self):
        """Pre-existing unencrypted rows are readable without error (migration window)."""
        enc = EncryptedField(_TEST_KEY)
        plaintext_metadata = json.dumps({"legacy": "row"})

        fake_row = _make_fake_row(metadata=plaintext_metadata)
        pool, _ = _make_mock_pool(fetch_return=[fake_row])
        storage = PostgresAuditStorage(pool=pool, encryption=enc)

        results = await storage.query()
        assert results[0].metadata == {"legacy": "row"}

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_query_handles_null_metadata(self):
        """NULL metadata column returns an empty dict, not an error."""
        enc = EncryptedField(_TEST_KEY)
        fake_row = _make_fake_row(metadata=None)
        pool, _ = _make_mock_pool(fetch_return=[fake_row])
        storage = PostgresAuditStorage(pool=pool, encryption=enc)

        results = await storage.query()
        assert results[0].metadata == {}


# ── Helpers for PostgresAuditStorage tests ────────────────────────────────────


def _extract_call_arg(mock_fn: AsyncMock, pos: int):
    """Return (sql, args[pos]) from the first call to an AsyncMock."""
    call_args = mock_fn.call_args
    positional = call_args[0]
    return positional[0], positional[pos]


def _make_fake_row(metadata: str | None) -> dict:
    """Minimal asyncpg-like row dict for query() reconstruction."""
    return {
        "id": str(uuid.uuid4()),
        "event_type": "test.event",
        "agent_id": "agent-test",
        "user_id": None,
        "action": "test.action",
        "outcome": "APPROVED",
        "risk_score": 0.1,
        "metadata": metadata,
        "trace_id": None,
        "approver_id": None,
        "created_at": datetime.now(UTC),
    }
