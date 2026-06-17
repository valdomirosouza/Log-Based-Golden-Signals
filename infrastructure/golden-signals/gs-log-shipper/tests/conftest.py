"""Pytest path bootstrap for the gs-log-shipper component.

The repo's `make test-unit-python` target is scoped to `tests/unit/` and its
coverage source is `src/` (pyproject.toml) — it does NOT cover
`infrastructure/golden-signals/`. These tests are therefore run directly
against this dir (see reports/.../logs/6-development.log for the make-target
coverage-gap note). This conftest puts the component dir on sys.path so the
`gs_log_shipper` package imports the same way it does inside the container.
"""

from __future__ import annotations

import sys
from pathlib import Path

_COMPONENT_ROOT = Path(__file__).resolve().parents[1]
if str(_COMPONENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_COMPONENT_ROOT))
