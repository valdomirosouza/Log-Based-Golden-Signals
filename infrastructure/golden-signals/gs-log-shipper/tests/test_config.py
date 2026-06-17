"""Config env-parsing unit tests for gs-log-shipper. Issue #24 (B-10)."""

from __future__ import annotations

import pytest
from gs_log_shipper.config import ShipperConfig, _float, _int


def test_int_defaults_and_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("X_INT", raising=False)
    assert _int("X_INT", 7) == 7
    monkeypatch.setenv("X_INT", "")
    assert _int("X_INT", 7) == 7
    monkeypatch.setenv("X_INT", "nope")
    assert _int("X_INT", 7) == 7
    monkeypatch.setenv("X_INT", "12")
    assert _int("X_INT", 7) == 12


def test_float_defaults_and_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("X_FLT", raising=False)
    assert _float("X_FLT", 1.5) == 1.5
    monkeypatch.setenv("X_FLT", "bad")
    assert _float("X_FLT", 1.5) == 1.5
    monkeypatch.setenv("X_FLT", "2.25")
    assert _float("X_FLT", 1.5) == 2.25


def test_from_env_full_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GS_API_KEYS", "key-a, key-b")
    monkeypatch.setenv("GS_INGESTION_URL", "http://svc:8085/ingestion")
    monkeypatch.setenv("GS_SHIPPER_BATCH_MAX_ENTRIES", "50")
    cfg = ShipperConfig.from_env()
    assert cfg.api_key == "key-a"
    assert cfg.ingestion_url == "http://svc:8085/ingestion"
    assert cfg.batch_max_entries == 50


def test_from_env_blank_key_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GS_API_KEYS", "   ,  ")
    with pytest.raises(ValueError, match="GS_API_KEYS is required"):
        ShipperConfig.from_env()


def test_syslog_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # ADR-0084 Amendment 2026-06-17: syslog listener bind, gs-net only.
    monkeypatch.setenv("GS_API_KEYS", "k1")
    monkeypatch.delenv("GS_SHIPPER_SYSLOG_HOST", raising=False)
    monkeypatch.delenv("GS_SHIPPER_SYSLOG_PORT", raising=False)
    cfg = ShipperConfig.from_env()
    # 0.0.0.0 is intentional: gs-net-only container, never host-published.
    assert cfg.syslog_host == "0.0.0.0"
    assert cfg.syslog_port == 514


def test_syslog_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GS_API_KEYS", "k1")
    monkeypatch.setenv("GS_SHIPPER_SYSLOG_HOST", "127.0.0.1")
    monkeypatch.setenv("GS_SHIPPER_SYSLOG_PORT", "5514")
    cfg = ShipperConfig.from_env()
    assert cfg.syslog_host == "127.0.0.1"
    assert cfg.syslog_port == 5514
