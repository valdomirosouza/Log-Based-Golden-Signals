"""Pytest path bootstrap for the gs-traffic-generator component.

`make test-unit-python` (testpaths=tests/unit, coverage source=src) does not
cover `infrastructure/golden-signals/`; these tests are run directly against
this dir (see reports/.../logs/6-development.log coverage-gap note). This puts
the component dir on sys.path so `gs_traffic_generator` imports as it does in
the container.
"""

from __future__ import annotations

import sys
from pathlib import Path

_COMPONENT_ROOT = Path(__file__).resolve().parents[1]
if str(_COMPONENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_COMPONENT_ROOT))
