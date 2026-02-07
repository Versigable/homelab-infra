# DevOps Operations Runbook (Homelab)

This is the **boss-level** runbook: the practical “what do I do now?” guide for operating homelab services on Linux hosts.

Scope:
- VM/LXC hosts running Docker/Compose services
- troubleshooting, recovery, and safe operational habits
- **not** GitOps/CI mechanics (those live in GitOps docs)

---

## 0) The Prime Directive

1) **Don’t make it worse.**  
2) **Collect evidence before changing state.**  
3) **Change one variable at a time.**  
4) **When in doubt: snapshot VM/LXC first.**

---

## 1) Quick Triage (5 minutes)

### 1.1 Is the host up?
```bash
uptime
who -a
ip a
ip r
```

### 1.2 Is disk full? (Top cause of “mystery outages”)
```bash
df -h
df -i
sudo du -xhd1 /var | sort -h | tail
sudo du -xhd1 /home | sort -h | tail
```

### 1.3 Is time correct? (SSO/TLS/cluster weirdness)
```bash
timedatectl
```

### 1.4 Is docker healthy?
```bash
sudo systemctl status docker --no-pager
sudo docker info | sed -n '1,80p'
sudo docker ps --format 'table {.Names}	{.Status}	{.Image}	{.Ports}'
```

### 1.5 Is the service stack healthy?
```bash
cd /home/<svc>/<svc>-hub/compose
sudo docker compose ps
sudo docker compose logs --tail=200
```

---

## 2) The “Golden Commands” (Copy/Paste Shelf)

### 2.1 What is listening?
```bash
ss -tulpn | head -n 80
```

### 2.2 Recent logs (system)
```bash
sudo journalctl -S -1h -p warning --no-pager
sudo journalctl -u docker -S -2h --no-pager
```

### 2.3 Container logs (focused)
```bash
sudo docker logs --tail 200 <container>
sudo docker logs -f <container>
```

### 2.4 Compose lifecycle
```bash
sudo docker compose pull
sudo docker compose up -d
sudo docker compose restart <service>
sudo docker compose down
```

### 2.5 Validate compose without changing state
```bash
sudo docker compose config >/tmp/compose.rendered.yml
```

---

## 3) Standard Operating Procedures (SOPs)

## 3.1 Safe restart procedure (single service)
1) Check health:
```bash
sudo docker compose ps
```
2) Tail logs:
```bash
sudo docker compose logs -f --tail=200 <service>
```
3) Restart:
```bash
sudo docker compose restart <service>
```

**If it doesn’t recover:** move to rollback/redeploy procedures.

---

## 3.2 Safe update procedure (per stack)
1) Snapshot VM/LXC (if risky change)
2) Pull + apply:
```bash
sudo docker compose pull
sudo docker compose up -d
```
3) Verify:
```bash
sudo docker compose ps
sudo docker compose logs --tail=200
```

---

## 3.3 Safe “down” procedure (avoid data loss)
If data lives in bind mounts under `data/`, you can safely take stack down:
```bash
sudo docker compose down
```

If you’re unsure about volumes:
```bash
sudo docker compose config | grep -n "volumes:" -n
sudo docker volume ls | head
```

---

## 4) Common Failure Modes (and How to Win)

## 4.1 “Job succeeded but service didn’t change”
Usually one of:
- wrong target path
- updated the wrong file
- service is running a different compose stack than you think

Confirm:
```bash
pwd
ls -la
sudo docker compose ls
sudo docker compose ps
```

## 4.2 Containers restarting in a loop
Get the last ~200 lines:
```bash
sudo docker logs --tail 200 <container>
```

Common causes:
- bad env var
- port already in use
- config parse error
- upstream dependency down
- permission issue on bind-mounted path

## 4.3 Port already in use
```bash
sudo ss -tulpn | grep -E ':<port>\b'
sudo docker ps --format 'table {.Names}	{.Ports}'
```

## 4.4 Permission denied on bind mount
Check ownership + perms:
```bash
namei -l /home/<svc>/<svc>-hub
sudo ls -la /home/<svc>/<svc>-hub/data
getfacl -p /home/<svc>/<svc>-hub | sed -n '1,80p'
```

## 4.5 “No space left on device”
- check docker logs / image bloat:
```bash
sudo docker system df
sudo docker image prune -a
sudo docker volume prune
```

**Careful:** pruning can remove caches/unused volumes. Only do this when you understand what’s unused.

---

## 5) Rollback Playbook (When you need to revert fast)

### 5.1 Fast rollback idea
If you keep a “last known good” snapshot of:
- compose yaml
- `.env`
- config directory

You can restore quickly. Practical approach:
- keep a root-only archive under:
  - `/home/<svc>/<svc>-hub/backups/last-good/`

### 5.2 Manual rollback steps
1) Stop stack:
```bash
cd /home/<svc>/<svc>-hub/compose
sudo docker compose down
```
2) Restore files from last-good archive
3) Start:
```bash
sudo docker compose up -d
sudo docker compose ps
```

> If you *don’t* have last-good: snapshot restoration at VM/LXC level becomes your “big hammer.”

---

## 6) Networking Debug Runbook

### 6.1 Local reachability
```bash
ping -c 2 1.1.1.1
ping -c 2 <gateway-ip>
curl -I http://<service-ip>:<port> 2>/dev/null | head
```

### 6.2 DNS sanity
```bash
getent hosts <name>
resolvectl status 2>/dev/null | sed -n '1,120p'
cat /etc/resolv.conf
```

### 6.3 Trace path
```bash
traceroute -n <target-ip> 2>/dev/null | head -n 20
```

### 6.4 Packet capture (surgical)
```bash
sudo tcpdump -ni any host <ip> and port <port>
```

---

## 7) Security / Access “Panic Page”

### 7.1 Who logged in?
```bash
last -a | head
sudo journalctl -u ssh -S -24h --no-pager | tail -n 200
```

### 7.2 Check authorized keys changes
```bash
find /home -maxdepth 3 -name authorized_keys -print -exec ls -l {} \;
```

### 7.3 Confirm sudoers allowlists
```bash
ls -l /etc/sudoers.d
sudo visudo -c
```

### 7.4 If compromise suspected (minimal steps)
1) isolate host (pfSense rule or host firewall)
2) snapshot VM/LXC
3) rotate secrets used on that host
4) review logs + changes
5) rebuild from known-good

---

## 8) Performance & Capacity Runbook

### 8.1 CPU/RAM pressure
```bash
free -h
top -o %CPU
ps aux --sort=-%mem | head
```

### 8.2 Disk IO
```bash
iostat -xz 1 3 2>/dev/null
```

### 8.3 Docker-specific hotspots
```bash
sudo docker stats --no-stream
sudo docker system df
```

---

## 9) Standard Checklists

### 9.1 After any change
- [ ] `docker compose ps` shows healthy
- [ ] logs are clean (no repeated errors)
- [ ] external access works (if applicable)
- [ ] disk not trending to 100%
- [ ] no new open ports

### 9.2 Monthly “housekeeping”
- [ ] apply OS security updates
- [ ] prune unused docker images (if safe)
- [ ] verify backups are actually running
- [ ] spot-check service runbooks

---

## 10) “Operator Quality-of-Life” Add-ons (Highly Recommended)

- `tmux` on all hosts
- consistent shell prompt showing host + user
- aliases:
  - `dps` = `sudo docker ps ...`
  - `dcl` = `sudo docker compose logs --tail=200`
- a tiny `ops/` folder in each hub with:
  - known-good commands
  - restore notes
  - contact list for collaborators

---

## Appendix A: The Hub Contract (Reminder)

```text
/home/<svc>/<svc>-hub/
├─ compose/
├─ config/
├─ data/
├─ logs/
└─ backups/
```

Treat this as the “unit of operation.”

---

## Appendix B: Minimal “Per Service” Runbook Template

Create: `/home/<svc>/<svc>-hub/README.md`

```markdown
# <service>

## Status
- compose dir: /home/<svc>/<svc>-hub/compose
- ports: <...>
- depends on: <...>

## Commands
```bash
cd /home/<svc>/<svc>-hub/compose
sudo docker compose ps
sudo docker compose logs --tail=200
sudo docker compose up -d
```

## Data
- data: /home/<svc>/<svc>-hub/data

## Notes
- <gotchas>
```
