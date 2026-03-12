"""Shim module to expose cozo_helpers at top-level for scripts that import it.
This dynamically loads the implementation from Scripts/Enrich/cozo_helpers.py so that
`import cozo_helpers` works regardless of CWD or sys.path.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_IMPL_PATH = Path(__file__).resolve().parent / "Scripts" / "Enrich" / "cozo_helpers.py"
if not _IMPL_PATH.exists():
    raise ImportError(f"cozo_helpers implementation not found at {_IMPL_PATH}")

spec = importlib.util.spec_from_file_location("_cozo_helpers_impl", str(_IMPL_PATH))
_impl = importlib.util.module_from_spec(spec)
loader = spec.loader
if loader is None:
    raise ImportError("Failed to load cozo_helpers implementation")
loader.exec_module(_impl)

# Export names from implementation
for attr in dir(_impl):
    if attr.startswith("__"):
        continue
    globals()[attr] = getattr(_impl, attr)

# Also register the implementation module under the expected name
sys.modules[__name__] = sys.modules.get(__name__, _impl)
