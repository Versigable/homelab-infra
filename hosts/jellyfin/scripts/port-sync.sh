#!/bin/sh
# Watches gluetun forwarded port file and updates qBittorrents listening port via WebUI API.
# Runs as a sidecar in gluetun netns so localhost:8080 = qBit.
# qBit must have "Bypass authentication for clients on localhost" enabled.
set -u

PORT_FILE=/gluetun/forwarded_port
QBIT_URL=http://localhost:8080

apk add --no-cache curl >/dev/null 2>&1 || true

last=""
while true; do
  if [ -f "$PORT_FILE" ]; then
    port=$(cat "$PORT_FILE" 2>/dev/null || echo "")
    if [ -n "$port" ] && [ "$port" != "$last" ]; then
      echo "[port-sync] gluetun forwarded port: $port"
      if curl -fsS -X POST \
           --data-urlencode "json={\"listen_port\":$port}" \
           "$QBIT_URL/api/v2/app/setPreferences" >/dev/null; then
        echo "[port-sync] qbit listening port updated to $port"
        last="$port"
      else
        echo "[port-sync] qbit API call failed (qBit starting, or auth-bypass disabled)"
      fi
    fi
  fi
  sleep 60
done
