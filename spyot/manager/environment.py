"""Cross-platform Python virtual-environment management.

The manager uses this to:
  * check whether a venv already exists and matches the requested Python
    major.minor version, and
  * create it (with pip + requirements) if not.

The *same* logic runs on the controller (local) and is mirrored on remote hosts
during deployment, because virtualenvs are not portable across machines/OSes -
they must be rebuilt on each target.

Pure standard library, so the manager can bootstrap an environment before any
third-party package is installed. Works on Windows, macOS, and Linux.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple


class EnvironmentManager:
    def __init__(self, project_root: str | Path, venv_dir: str,
                 python_version: str, requirements: str = "requirements.txt") -> None:
        self.project_root = Path(project_root).resolve()
        self.venv_path = (self.project_root / venv_dir).resolve()
        self.python_version = python_version  # e.g. "3.11"
        self.requirements = requirements

    # -- venv interpreter path --------------------------------------------- #

    @property
    def venv_python(self) -> Path:
        if os.name == "nt":
            return self.venv_path / "Scripts" / "python.exe"
        return self.venv_path / "bin" / "python"

    # -- inspection -------------------------------------------------------- #

    def exists(self) -> bool:
        return self.venv_python.exists()

    def current_version(self) -> Optional[str]:
        """Return the 'major.minor' of the venv interpreter, or None."""
        if not self.exists():
            return None
        try:
            out = subprocess.check_output(
                [str(self.venv_python), "-c",
                 "import sys;print('%d.%d' % sys.version_info[:2])"],
                text=True,
            ).strip()
            return out
        except (subprocess.CalledProcessError, OSError):
            return None

    def is_valid(self) -> bool:
        """True if the venv exists and matches the requested major.minor."""
        ver = self.current_version()
        return ver is not None and ver == self.python_version

    # -- base interpreter discovery ---------------------------------------- #

    def find_base_python(self) -> Optional[List[str]]:
        """Locate a base interpreter matching ``python_version``.

        Returns the command (as an argv list) or None. Honors an explicit
        override via the ``SPYOT_PYTHON`` environment variable.
        """
        override = os.environ.get("SPYOT_PYTHON")
        if override:
            return [override]

        major_minor = self.python_version
        candidates: List[List[str]] = []

        if os.name == "nt":
            # Windows: prefer the py launcher, then bare pythons.
            candidates += [["py", f"-{major_minor}"], ["py", f"-{major_minor[0]}"]]
            candidates += [["python"], ["python3"]]
        else:
            candidates += [[f"python{major_minor}"], [f"python{major_minor[0]}"]]
            candidates += [["python3"], ["python"]]

        for cmd in candidates:
            ver = self._probe_version(cmd)
            if ver == major_minor:
                return cmd
        # Fall back to any interpreter, but warn the caller via None match on
        # version; we still return the first that runs so creation can proceed.
        for cmd in candidates:
            if self._probe_version(cmd) is not None:
                return cmd
        return None

    @staticmethod
    def _probe_version(cmd: List[str]) -> Optional[str]:
        if shutil.which(cmd[0]) is None and not Path(cmd[0]).exists():
            return None
        try:
            out = subprocess.check_output(
                cmd + ["-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
                text=True, stderr=subprocess.STDOUT,
            ).strip()
            # Last line guards against launchers printing banners.
            return out.splitlines()[-1].strip()
        except (subprocess.CalledProcessError, OSError):
            return None

    # -- creation ---------------------------------------------------------- #

    def create(self, force: bool = False) -> None:
        if self.exists() and not force:
            return
        if force and self.venv_path.exists():
            shutil.rmtree(self.venv_path)

        base = self.find_base_python()
        if base is None:
            raise RuntimeError(
                f"Could not find a Python {self.python_version} interpreter. "
                f"Install it, or set SPYOT_PYTHON to its path."
            )
        got = self._probe_version(base)
        if got != self.python_version:
            print(f"[env] WARNING: requested Python {self.python_version} but "
                  f"the best available base is {got} ({' '.join(base)}).")

        print(f"[env] creating venv at {self.venv_path} using {' '.join(base)}")
        subprocess.check_call(base + ["-m", "venv", str(self.venv_path)])

        # Upgrade pip then install requirements if present.
        subprocess.check_call(
            [str(self.venv_python), "-m", "pip", "install", "--upgrade", "pip"])
        req_path = self.project_root / self.requirements
        if req_path.exists():
            print(f"[env] installing requirements from {req_path}")
            subprocess.check_call(
                [str(self.venv_python), "-m", "pip", "install", "-r", str(req_path)])
        else:
            print(f"[env] no requirements file at {req_path}; skipping installs")

    def ensure(self, force: bool = False) -> Tuple[bool, str]:
        """Create the venv if needed. Returns (created, message)."""
        if not force and self.is_valid():
            return False, f"venv OK at {self.venv_path} (Python {self.current_version()})"
        self.create(force=force)
        return True, f"venv ready at {self.venv_path} (Python {self.current_version()})"
