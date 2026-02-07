# ServerAdmin.md — Gaming Servers Runbook

_Last updated: 2025‑09‑03_

This is the canonical runbook for administering game servers on **GamingHub**. It covers common standards and game‑specific notes for **Sons of the Forest**, **Valheim**, **Minecraft Bedrock**, and a reserved section for **Satisfactory**.

---

## 0) Conventions & Layout
```
/home/<svc>/<proj>-hub/
├─ compose/                 # docker-compose.yml + .env (600)
├─ config/                  # per-game configs
│  ├─ minecraft/
│  ├─ valheim/
│  └─ sotf/
├─ data/                    # world saves, persistent game state
├─ logs/
└─ backups/
```
- **Compose:** run from `compose/` (`docker compose up -d` etc.)
- **Ownership:** service user owns the hub; group `devops` has write (see DevOps perms doc). `.env` and tokens are **600**.
- **Change pattern:**
  1. `docker compose config`
  2. `docker compose pull <svc>` (optional)
  3. `docker compose up -d <svc>`
  4. `docker compose logs --tail=120 <svc>`

---

## 1) Saving & Persistence (Important)
Different games have **world** vs **player** data:
- **World save (server‑side):** The map, bases, environment. Usually autosaved by the server on an interval and on clean shutdown.
- **Player save (client‑side or per‑SteamID):** Position, inventory, stats. Some titles (e.g., **Sons of the Forest**) only persist these when the player **uses a bed/save point** or cleanly exits.

Admin tips:
- Set sensible `SaveInterval` (SotF) or equivalent autosave timers.
- Encourage players to **save in‑game** before disconnecting on titles that require it.
- For backups, prefer **app‑level archives** of world folders (see §3) so you can roll back without restoring a whole VM.

---

## 2) Healthchecks, Graceful Stops, and Log Caps
### Healthcheck (Compose)
Add per‑service healthchecks so you can detect stalls:
```yaml
services:
  <svc>:
    healthcheck:
      test: ["CMD-SHELL", "ss -lunp | grep -qE ':(8766|9700|27016)\\b'"]  # example: SOTF
      interval: 30s
      timeout: 3s
      retries: 10
    restart: unless-stopped
```
> Docker won’t auto‑restart on an `unhealthy` state by itself. Pair this with the watchdog below.

### Watchdog (cron)
Simple cron that restarts a container if it’s unhealthy:
```bash
# /usr/local/sbin/restart-if-unhealthy.sh
#!/usr/bin/env bash
set -euo pipefail
SVC="${1:-sotf}"
if [[ "$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$SVC" 2>/dev/null)" == "unhealthy" ]]; then
  echo "[$(date -Is)] restarting $SVC (unhealthy)" | systemd-cat -t docker-watchdog
  docker restart "$SVC"
fi
```
```
chmod +x /usr/local/sbin/restart-if-unhealthy.sh
# run every 2 minutes
*/2 * * * * root /usr/local/sbin/restart-if-unhealthy.sh sotf
```

### Graceful stop window
Give servers time to flush saves before SIGKILL:
```yaml
services:
  <svc>:
    stop_grace_period: 90s
```

### Cap container logs
Per‑service (preferred):
```yaml
services:
  <svc>:
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```
Host‑wide (optional) `/etc/docker/daemon.json`:
```json
{ "log-driver": "json-file", "log-opts": {"max-size":"10m", "max-file":"3"} }
```
Restart the daemon to apply host‑wide defaults.

---

## 3) Backups (VM‑level + App‑level)
**Two layers** are ideal:
1) **VM/LXC nightly** (Proxmox) → disaster recovery.
2) **App‑level archives** (tar world + configs) → quick rollbacks and no “smear”.

Example app‑level backup for SOTF (slot‑aware), rotate 14 days:
```bash
# /usr/local/sbin/sotf-backup.sh
#!/usr/bin/env bash
set -euo pipefail
SRC="/home/sotf/sotf-hub/data/userdata"
DST="/home/sotf/backups/sotf"
ts=$(date +%F_%H%M)
mkdir -p "$DST"
# fast archive; consider pausing or scheduling right after an autosave
tar czf "$DST/slot${SAVE_SLOT:-1}_${ts}.tgz" -C "$SRC" .
find "$DST" -type f -mtime +14 -delete
```
```
chmod +x /usr/local/sbin/sotf-backup.sh
0 */6 * * * root SAVE_SLOT=1 /usr/local/sbin/sotf-backup.sh
```
> For zero‑smear backups, schedule near autosave or during a brief maintenance restart.

---

## 4) Discord webhooks (maintenance messages)
1. In your Discord **Server Settings → Integrations → Webhooks → New Webhook**.
2. Choose a channel, copy the **Webhook URL**.
3. Store it in `compose/.env` for the service (file mode **600**):
```
DISCORD_WEBHOOK=https://discord.com/api/webhooks/…
```
4. Test:
```bash
curl -sS -H 'Content-Type: application/json' \
  -d '{"content": ":white_check_mark: GamingHub webhook test"}' "$DISCORD_WEBHOOK"
```

**Maintenance window script (optional):**
```bash
# /usr/local/sbin/sotf-maint-window.sh
#!/usr/bin/env bash
set -euo pipefail
WEBHOOK="${DISCORD_WEBHOOK:-}"
say(){ [ -z "$WEBHOOK" ] || curl -sS -H 'Content-Type: application/json' -d "{\"content\":\"$1\"}" "$WEBHOOK" >/dev/null; }

echo "Schedule a 5-min notice, then restart pull"
say ":tools: SotF maintenance in 5 minutes — please SAVE at a bed."; sleep 300
say ":repeat: Restarting now (brief)."
cd /home/sotf/sotf-hub/compose
/docker compose pull sotf || true
/docker compose up -d sotf
say ":white_check_mark: SotF back up."
```
Cron it at 04:00:
```
0 4 * * * root . /home/sotf/sotf-hub/compose/.env; /usr/local/sbin/sotf-maint-window.sh
```

---

## 5) Game‑Specific Runbooks

### A) Sons of the Forest (SOTF)
**Ports (UDP):** 8766 (game), **9700 (BlobSync/world)**, 27016 (server list), 27015 (Steam query).

**Config file:** `config/sotf/dedicatedserver.cfg` (JSON). Key fields:
```json
{
  "IpAddress": "0.0.0.0",
  "GamePort": 8766,
  "QueryPort": 27016,
  "BlobSyncPort": 9700,
  "ServerName": "<name>",
  "LanOnly": false,
  "DisableSteamNetworking": false,
  "ServerSteamAccount": "<GSLT>",
  "SaveInterval": 300,
  "IdleTargetFramerate": 5,
  "ActiveTargetFramerate": 60,
  "LogFilesEnabled": true,
  "TimestampLogFilenames": true
}
```
**Notes**
- **Player saving:** players must **save at a bed** (or clean exit) to persist inventory/position.
- **Autosave** controls **world** persistence only.
- `Active/IdleTargetFramerate` tune CPU load of the server’s main loop; lower idle = cooler when empty.

**Compose service template**
```yaml
services:
  sotf:
    image: cm2network/sonsoftheforest
    container_name: sotf
    restart: unless-stopped
    stop_grace_period: 90s
    environment:
      - ALWAYS_UPDATE_ON_START=false
      - SKIP_NETWORK_ACCESSIBILITY_TEST=true
    volumes:
      - ../config/sotf/dedicatedserver.cfg:/sonsoftheforest/userdata/dedicatedserver.cfg:ro
      - ../data/userdata:/sonsoftheforest/userdata
    ports:
      - "8766:8766/udp"
      - "9700:9700/udp"
      - "27015:27015/udp"
      - "27016:27016/udp"
    healthcheck:
      test: ["CMD-SHELL", "ss -lunp | grep -qE ':(8766|9700|27016)\\b'"]
      interval: 30s
      timeout: 3s
      retries: 10
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "3" }
```
**Ops**
```
cd /home/sotf/sotf-hub/compose
docker compose up -d sotf
docker compose logs -f --tail=200 sotf
ss -lunp | egrep ':(8766|9700|27015|27016)\b'
```
**Common issues**
- Listed in Steam but can’t join → usually **9700/udp** not open through the edge.
- Token hiccups → ensure GSLT is for app **1326470** and quoted in JSON.

---

### B) Valheim
**Ports (UDP):** 2456–2458.

**Compose** (example):
```yaml
services:
  valheim:
    image: lloesche/valheim-server
    container_name: valheim
    restart: unless-stopped
    environment:
      - SERVER_NAME=Vulfkube-World
      - WORLD_NAME=vulfkube
      - SERVER_PASS=<set>
    volumes:
      - ../data:/config
    ports:
      - "2456:2456/udp"
      - "2457:2457/udp"
      - "2458:2458/udp"
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "3" }
```
**Ops**: same pattern as SOTF. Ensure 3 UDP ports are open end‑to‑end.

---

### C) Minecraft Bedrock
**Ports (UDP):** 19132 (primary), 19142 (secondary world).

**Compose** (example):
```yaml
services:
  bedrock-1:
    image: itzg/minecraft-bedrock-server
    container_name: bedrock1
    environment:
      - EULA=TRUE
      - SERVER_NAME=OG-World
    volumes:
      - ../data/og-world:/data
    ports:
      - "19132:19132/udp"
    restart: unless-stopped
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "3" }
```

---

### D) Satisfactory (reserved)
**Ports (UDP):** 7777, 15000, 15777. Runbook to be added when we enable the container.

---

## 6) Networking (Edge summary)
- pfSense WAN → UDP NAT alias to **Traefik** → Traefik UDP → **GamingHub**.
- Traefik static has UDP **entryPoints** for all game ports; dynamic YAML routes to `10.0.1.9`.
- Split‑horizon DNS points hostnames to Traefik.

---


