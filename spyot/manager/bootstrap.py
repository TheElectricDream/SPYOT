"""Ensure the manager's own third-party dependencies are importable.

The manager needs PyYAML (to read the user's YAML config) and paramiko (for
remote deploy/sync). These are *not* needed for stdlib-only operations (local
env management and local runs), so we only require them on demand and can
optionally auto-install them into the current interpreter.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from typing import Iterable, List

# import name -> pip requirement
MANAGER_DEPS = {
    "yaml": "pyyaml",
    "paramiko": "paramiko",
}


def missing(deps: Iterable[str]) -> List[str]:
    out = []
    for mod in deps:
        try:
            importlib.import_module(mod)
        except ImportError:
            out.append(mod)
    return out


def ensure(deps: Iterable[str], auto_install: bool = False) -> None:
    """Make sure ``deps`` import; optionally pip-install the missing ones."""
    need = missing(deps)
    if not need:
        return
    pkgs = [MANAGER_DEPS.get(m, m) for m in need]
    if not auto_install:
        raise RuntimeError(
            f"missing manager dependencies: {', '.join(pkgs)}. Install them with "
            f"'pip install {' '.join(pkgs)}' (or pass --auto-install)."
        )
    print(f"[bootstrap] installing manager deps: {', '.join(pkgs)}", flush=True)
    subprocess.check_call([sys.executable, "-m", "pip", "install", *pkgs])
    # Invalidate import caches so freshly installed packages are importable.
    importlib.invalidate_caches()
