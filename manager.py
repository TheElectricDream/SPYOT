#!/usr/bin/env python3
"""SPYOT manager entry point.

Run ``python manager.py --help`` for usage. This is a thin wrapper around
``spyot.manager.cli`` so the tool can be invoked without installation, and is
also what the deployer runs on remote hosts to (re)build their environments.
"""

import sys
from pathlib import Path

# Make the project importable when run directly from a checkout.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from spyot.manager.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
