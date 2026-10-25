"""Allow ``python -m pulseguard``."""

from __future__ import annotations

import sys

from pulseguard.cli import main

if __name__ == "__main__":
    sys.exit(main())
