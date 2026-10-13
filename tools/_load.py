"""Load integration modules by file path, without importing the Home Assistant package.

The component directory must not go on sys.path: it contains select.py / switch.py, which
would shadow the standard library's ``select`` module.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

COMPONENT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "godaikin"


def load(name: str) -> ModuleType:
    """Return ``custom_components/godaikin/<name>.py`` as a module (cached in sys.modules)."""
    qualified = f"godaikin_tools.{name}"
    if qualified in sys.modules:
        return sys.modules[qualified]
    spec = importlib.util.spec_from_file_location(qualified, COMPONENT_DIR / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    return module
