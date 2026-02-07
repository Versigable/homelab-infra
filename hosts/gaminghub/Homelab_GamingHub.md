# GamingHub — 10.0.1.9 (LXC ID 109)
**Generated:** 2025-09-01

## Overview
- **Role:** Game hosting hub (Minecraft, Valheim, Sons of the Forest).
- **IP:** 10.0.1.9
- **VMID:** 109
- **Ingress:** Players connect via Hetzner VPS (WireGuard + DNAT) → GamingHub.
- **Auth:** No Authentik middleware (console clients like Xbox/Switch can’t auth).
- **Status:** Game containers reachable; config tuning in progress.

---

## Project Layout
```
/home/<svc>/<proj>-hub/
├─ compose/                 # docker-compose.yml + .env (600)
├─ config/                  # per-game configs
│  ├─ minecraft/
│  ├─ valheim/
│  └─ sotf/
├─ data/                    # world saves, persistent game state
├─ logs/
├─ backups/
└─ legacy/                  # imported Linode data (to be pruned)
```

### Current Services
- `/home/minecraft/minecraft-hub/`
  - **OG-World** (Bedrock) → external port **19142/udp**
  - **Firekube-World** (Bedrock) → external port **19132/udp**
- `/home/valheim/valheim-hub/`
  - **Vulfkube-World** → external ports **2456–2458/udp**
- `/home/sotf/sotf-hub/`
  - **Sons of the Forest** → external ports **8766/udp**, **27015/udp**, **27016/udp**

> NOTE: External ports refer to the Hetzner VPS public ports that are DNAT’d to GamingHub’s 10.0.1.9.

---

## Ownership & Permissions
- Service users: `minecraft`, `valheim`, `sotf` — each owns its own hub tree.
- Group: **devops** (collaboration).
- Layout standardized with your devops helper scripts.
- `.env` and tokens → **chmod 600**; consider removing `g:devops` ACLs for token files.

Quick template:
```bash
# example for a hub root
chown -R minecraft:devops /home/minecraft/minecraft-hub
find /home/minecraft/minecraft-hub -type d -exec chmod 2750 {} \;
find /home/minecraft/minecraft-hub -type f -exec chmod 0640 {} \;
chmod 600 /home/minecraft/minecraft-hub/compose/.env 2>/dev/null || true
```

---

## Networking
External access is via the Hetzner VPS (“WireGuardGaming”) with **DNAT** + **masquerade** back to GamingHub.

### Known-good rules on the VPS
- DNAT examples (ip nat *prerouting* → 10.0.1.9):
  - Bedrock **19132/udp** → 10.0.1.9:19132
  - Bedrock **19142/udp** → 10.0.1.9:19142
  - Valheim **2456–2458/udp** → 10.0.1.9:2456–2458
  - SotF **8766/udp**, **27015/udp**, **27016/udp** → 10.0.1.9 (same ports)

- **Postrouting** (ip nat):
  - Keep both masquerades (these were required in practice):
    ```
    oifname "eth0" masquerade
    oifname "wg0"  masquerade
    ```

> If wg0 masquerade is removed, traffic stops reaching GamingHub. Keep both until/unless you rework policy routing.

### GamingHub host firewall (recommended)
Install UFW and allow only the required ports:
```bash
sudo apt-get update && sudo apt-get install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 19132/udp
sudo ufw allow 19142/udp
sudo ufw allow 2456:2458/udp
sudo ufw allow 8766/udp
sudo ufw allow 27015/udp
sudo ufw allow 27016/udp
sudo ufw enable
sudo ufw status verbose
```

---

## Runbook

### Lifecycle
```bash
# Restart or update a service
cd /home/<svc>/<proj>-hub/compose
docker compose pull
docker compose up -d
docker compose ps
```

### Logs & Health
```bash
docker compose logs --tail=200 <service>
docker ps --format 'table {{.Names}}	{{.Status}}	{{.Ports}}'
```

### Packet checks (end-to-end)
On VPS:
```bash
sudo tcpdump -ni eth0 udp port 19132 or 19142 or 2456 or 2457 or 2458 or 8766 or 27015 or 27016
sudo tcpdump -ni wg0  udp port 19132 or 19142 or 2456 or 2457 or 2458 or 8766 or 27015 or 27016
```
On GamingHub:
```bash
sudo tcpdump -ni wg0  udp port 19132 or 19142 or 2456 or 2457 or 2458 or 8766 or 27015 or 27016
sudo tcpdump -ni any  udp port 19132 or 19142 or 2456 or 2457 or 2458 or 8766 or 27015 or 27016
```

---

## Service Notes

### Minecraft (Bedrock)
- OG-World & Firekube are distinct containers / worlds.
- Common Bedrock settings in `config/minecraft/` (recommend centralizing there).
- World data in `/data/` (mount into containers).
- Exposed ports: **19132/udp**, **19142/udp**.

### Valheim
- Uses three UDP ports (game, query, and steam): **2456–2458/udp**.
- Confirm `SERVER_NAME`, `WORLD_NAME`, and `PASSWORD` in compose env.
- For stability, pin the image tag to a known-good version once verified.

### Sons of the Forest (SotF)
- **AppID:** 1326470 (correct). Ensure Steam token (GSLT) in `config/sotf/dedicatedserver.cfg`.
- Required ports: **8766/udp**, **27015/udp**, **27016/udp**.
- Ensure **LANOnly=false** if you want it publicly listed.
- If Steam “Favorites” shows the server but the game client can’t connect:
  - Double-check token validity and that it matches AppID 1326470.
  - Verify DNAT for all three ports.
  - Leave wg0+eth0 masquerades enabled on VPS.

---

## Backups
Nightly tar of worlds + configs; keep 7 daily, 4 weekly. Offload to TrueNAS (10.0.1.7).
```bash
# simple example
BACKUP_ROOT=/home/<svc>/<proj>-hub
ts=$(date +%F_%H%M)
tar czf /home/<svc>/backups/${ts}.tgz   ${BACKUP_ROOT}/config   ${BACKUP_ROOT}/data   ${BACKUP_ROOT}/compose/docker-compose.yml
find /home/<svc>/backups -type f -mtime +30 -delete
```

---

## To‑do / Recommendations
- [ ] Finish migrating all per-game configs into `config/<service>/` and mount read-only where possible.
- [ ] Add systemd user services or a watchtower alternative for controlled updates.
- [ ] Add a pre-flight script that asserts required DNATs are present on the VPS.
- [ ] After everything is stable, prune `/legacy/` directories from the Linode migration.

---

## Change Log (this node)
- **2025‑09‑01:** Initial doc, validated that **wg0 + eth0 masquerade** on VPS is necessary for forwarding to GamingHub. SotF token corrected to AppID 1326470.


## Overview (patched)
- **Ingress:** Internet → **pfSense** UDP NAT → **Traefik (10.0.2.5)** UDP entryPoints → **GamingHub (10.0.1.9)**. No Hetzner relay.

## Current Services (patched)
- **Minecraft (Bedrock)** — 19132/udp, 19142/udp
- **Valheim** — 2456–2458/udp
- **Sons of the Forest** — 8766/udp, **9700/udp**, 27016/udp, 27015/udp
- **Satisfactory** — (reserved) 7777/udp, 15000/udp, 15777/udp

## Runbooks (new)

### Sons of the Forest
- **Ports:** 8766, 9700, 27016, 27015 (UDP). Ensure pfSense alias and Traefik entryPoints cover all.
- **Saving:** world autosaves; players must save at a bed to persist inventory/position.
- **Ops:**
  ```bash
  cd /home/sotf/sotf-hub/compose
  docker compose up -d sotf && docker compose logs --tail=120 sotf
  ss -lunp | egrep ':(8766|9700|27015|27016)\b'
  ```
- **Maintenance:** `ALWAYS_UPDATE_ON_START=false`; use the Discord‑notified 04:00 window.

### Valheim
- **Ports:** 2456–2458/udp.
- **Ops:** regular compose cycle; three ports must be reachable end‑to‑end.

### Minecraft (Bedrock)
- **Ports:** 19132/udp (OG‑World), 19142/udp (Firekube).
- **Ops:** standard compose cycle; worlds live under `data/`.

### Satisfactory (reserved)
- **Ports:** 7777/udp, 15000/udp, 15777/udp.
- **Status:** not yet enabled; keep Traefik/pfSense entries ready.

## Logging caps (add per service)
```yaml
logging:
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"
```

## Healthcheck template
```yaml
healthcheck:
  test: ["CMD-SHELL", "ss -lunp | grep -qE ':(PORTS)\\b'"]
  interval: 30s
  timeout: 3s
  retries: 10
```

## Backups (node‑level + app‑level)
- Keep VM/LXC nightly backups in Proxmox for DR.
- Add app‑level tar archives of worlds/configs for quick rollbacks (see §3 above).

---

_End of patch._
