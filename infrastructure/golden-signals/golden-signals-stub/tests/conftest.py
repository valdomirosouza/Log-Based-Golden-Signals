"""Pytest path bootstrap for the golden-signals-stub component.

Not covered by `make test-unit-python` (src/ + tests/ only); run directly here.
See reports/.../logs/6-development.log for the make-target coverage-gap note.
"""

from __future__ import annotations

import sys
from pathlib import Path

_COMPONENT_ROOT = Path(__file__).resolve().parents[1]
if str(_COMPONENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_COMPONENT_ROOT))
