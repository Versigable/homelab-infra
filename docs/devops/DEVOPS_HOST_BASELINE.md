# DevOps Host Baseline (Homelab Standard)

This doc defines the **minimum baseline** for any VM/LXC that runs services in the homelab.  
Scope is **DevOps platform hygiene** (OS, users, SSH, logging, Docker, safety rails).  
It intentionally does **not** cover GitOps/CI specifics.

---

## 1) Host identity + networking

### 1.1 Naming
- Hostname should match your inventory naming convention (short + memorable).
- Each host must have a stable management address (e.g. `10.0.x.y`).
- Document:
  - host role (Traefik / Authentik / GamingHub / ServiceHub / GitLab)
  - CPU/RAM/Disk
  - VLAN/segment (if applicable)

### 1.2 Time + DNS + NTP
- Ensure time sync is on (chrony/systemd-timesyncd).
- Use consistent DNS resolvers across hosts (avoid “random” /etc/resolv.conf drift).
- Keep time correct: SSO and TLS will punish clock skew.

---

## 2) User model (least privilege)

### 2.1 Humans
- Humans have **their own** accounts (no shared logins).
- SSH keys only; keep passwords locked unless there’s a specific reason.

### 2.2 Service accounts
- One Linux user per service family (example: `traefik`, `authentik`, `wiki`, `minecraft`, etc.)
- Service users are:
  - `nologin` (unless interactive maintenance is required)
  - no password
  - not in `docker` group by default

### 2.3 Shared collaboration group
- `devops` group is used for controlled collaboration inside hubs.
- Permissioning/ACL standard is documented separately in **DEVOPS_COLLAB_PERMS.md**.

---

## 3) SSH baseline (hard mode but livable)

### 3.1 Key-only auth
- Prefer per-user key-only, no password auth.
- Keep `.ssh` perms strict:
  - `~/.ssh` 700
  - `authorized_keys` 600

### 3.2 Safe sshd defaults
Suggested approach: use `/etc/ssh/sshd_config.d/*.conf` fragments.

Recommended:
- Disable password auth (or do it per-user via Match blocks)
- Disable root login over SSH (or restrict to key-only + known source nets)
- Rate-limit: `MaxAuthTries`, `LoginGraceTime`
- Keep `AllowUsers` / `AllowGroups` if you want a tight allowlist

> Tip: if you’re experimenting with per-user key-only, use your helper script (`devops-user-keyonly.sh`).

---

## 4) Sudo baseline (restricted blast radius)

- Avoid “full sudo” for automation users.
- Prefer **allowlisting** only what’s needed (e.g., docker, install, rsync).
- Validate allowlist with the exact command, not `sudo -n true` (which may not be allowed).

Example pattern:
```sudoers
gitlab-runner ALL=(root) NOPASSWD: /usr/bin/docker
gitlab-runner ALL=(root) NOPASSWD: /usr/bin/install
gitlab-runner ALL=(root) NOPASSWD: /usr/bin/rsync
```

---

## 5) Filesystem conventions

### 5.1 Canonical service layout
Each service (or service bundle) lives under:

```text
/home/<svc>/<svc>-hub/
├─ compose/
├─ config/
├─ data/
├─ logs/
└─ backups/
```

Rules:
- Treat `/home/<svc>/<svc>-hub` as the **operational boundary**.
- Keep sensitive secrets out of repo-readable locations.
- If a file must be private (e.g. `.env`), make it root-only.

### 5.2 Permissions targets (practical)
- Hub directories: group-collab (usually `devops`) with setgid + default ACLs.
- Secrets: opt-out (root-only or remove devops ACL per-file).

---

## 6) Docker baseline (reliability + sanity)

### 6.1 Install + daemon hygiene
- Ensure docker + compose plugin are installed consistently across hosts.
- Enable docker daemon log rotation everywhere (local driver):
  - max-size: 10m
  - max-file: 3

Example `/etc/docker/daemon.json`:
```json
{
  "log-driver": "local",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
```

Restart docker after changes:
```bash
sudo systemctl restart docker
```

### 6.2 Compose hygiene
- Always pin compose files in one place: `.../compose/docker-compose.yml` (or service-specific naming where required).
- Prefer `restart: unless-stopped`.
- Add container healthchecks for anything user-facing.

---

## 7) Host logging + auditing

Minimum:
- `journalctl` is your source of truth.
- Ensure logs survive enough time to troubleshoot (consider persistent journal on critical hosts).
- For security audits:
  - record authorized_keys changes
  - keep “who can sudo what” visible (sudoers fragments in git where safe)

---

## 8) Patch cadence (don’t get owned)

- Establish a simple patch rhythm:
  - weekly: security updates
  - monthly: kernel/major updates where safe
- Reboot windows should be planned for edge services.

---

## 9) Host commissioning checklist

**Before putting a host into service:**
- [ ] hostname + IP documented
- [ ] time sync OK
- [ ] SSH key-only
- [ ] service account(s) created
- [ ] hub layout created
- [ ] docker installed + log rotation configured
- [ ] firewall baseline applied (host-level or pfSense)
- [ ] backup policy confirmed (VM/LXC-level snapshots/backups already in place)

---

## 10) “Cool next” upgrades

- Add `fail2ban` (or equivalent) for SSH on edge-reachable hosts
- Centralize logs (Loki/Promtail or similar)
- Add node exporter + dashboards for capacity planning
