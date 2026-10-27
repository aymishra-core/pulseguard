#!/bin/sh
# Create a virtualenv when this tree does not have one, then open PulseGuard.
set -eu
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -e .
exec .venv/bin/pulseguard serve "$@"
