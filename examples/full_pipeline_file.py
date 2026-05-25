"""Backwards-compatibility shim — file moved to examples/basic/pipeline_file.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "pipeline_file",
    Path(__file__).parent / "basic" / "pipeline_file.py",
)
import sys

assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules["pipeline_file"] = _mod  # must be registered before exec for @dataclass
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

DEMO_ITEMS = _mod.DEMO_ITEMS
DEMO_SCOPE = _mod.DEMO_SCOPE
file_backend_demo_stack = _mod.file_backend_demo_stack
run_file_backend_demo = _mod.run_file_backend_demo
