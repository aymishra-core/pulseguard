#!/bin/sh
# Pick a private address from /etc/hosts. Docker writes the container address there.
set -eu
host=$(python - <<'PY'
import ipaddress

chosen = "127.0.0.1"
try:
    handle = open("/etc/hosts", encoding="utf-8")
except OSError:
    print(chosen)
    raise SystemExit(0)
with handle:
    for line in handle:
        parts = line.split()
        if not parts or parts[0].startswith("#"):
            continue
        try:
            parsed = ipaddress.ip_address(parts[0])
        except ValueError:
            continue
        if (
            isinstance(parsed, ipaddress.IPv4Address)
            and parsed.is_private
            and not parsed.is_loopback
            and not parsed.is_unspecified
            and not parsed.is_link_local
        ):
            chosen = str(parsed)
            break
print(chosen)
PY
)
exec pulseguard web \
  --config "${PULSEGUARD_CONFIG:-/app/config/monitors.example.json}" \
  --db "${PULSEGUARD_DB:-/data/history.db}" \
  --host "$host" \
  --port "${PULSEGUARD_PORT:-8080}"
