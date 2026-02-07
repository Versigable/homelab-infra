# DevOps Service Standards (Layout, Runtime, Ops)

This doc standardizes how services are **laid out**, **run**, and **operated** across the homelab.
This is DevOps scope (service hygiene), not GitOps/CI.

---

## 1) The “Hub” layout (the contract)

Every service gets:

```text
/home/<svc>/<svc>-hub/
├─ compose/        # docker-compose.yml + .env (if used)
├─ config/         # static/dynamic config, templates
├─ data/           # persistent data volumes
├─ logs/           # bind-mounted logs when needed
└─ backups/        # optional app exports (VM backups still primary)
```

### Why this rocks
- predictable paths across hosts
- easy permissioning
- easy backup/restore targeting
- services are isolated by Linux user + directory boundary

---

## 2) Service accounts (one service = one Unix identity)

### 2.1 Defaults
For each service user:
- locked password
- `nologin` unless interactive maintenance is required
- owns its hub: `/home/<svc>/<svc>-hub`
- **not** in docker group by default

### 2.2 When to allow interactive shell
If you need interactive shell (rare), allow it explicitly and still keep password locked:
- auth is SSH key-only
- consider per-user ssh Match blocks (no password auth)

---

## 3) Compose conventions

### 3.1 Compose file location
- Primary compose lives at:
  - `/home/<svc>/<svc>-hub/compose/docker-compose.yml`
- If you have multiple compose stacks per host, keep them in the same folder with clear names, but maintain a single “entry” compose if possible.

### 3.2 Restart policy
Use:
```yaml
restart: unless-stopped
```

### 3.3 Networks
- Prefer a small set of named Docker networks rather than ad-hoc per compose.
- Document which services share networks (e.g., proxy network).

### 3.4 Healthchecks (strongly recommended)
Add healthchecks for anything important.
This makes:
- `docker compose ps` meaningful
- smoke tests straightforward
- restarts less mysterious

---

## 4) Environment variables + secrets (runtime standard)

### 4.1 `.env` permissions
If you use `.env` in `compose/`, treat it as a secret:
- `0600 root:root` is the strongest default
- if service user must read it, still keep it `0640` and remove devops ACL if needed

### 4.2 Secret sprawl rules
- Avoid placing tokens/keys in compose YAML.
- Prefer:
  - `.env` (strict perms)
  - Docker secrets (where appropriate)
  - mounted secret files with strict perms

---

## 5) Configuration management (without GitOps assumptions)

Even without GitOps, you should:
- keep config under `config/` (not scattered)
- keep “generated” config out of git when possible
- write down where config is loaded from (compose bind mount paths)

---

## 6) Operational runbooks (per-service)

Every service should have a **tiny runbook** (even 10 lines is enough):
- how to check status
- how to view logs
- where data lives
- how to restart safely
- known ports + dependencies
- “oh no” restore steps

A runbook file in the service hub is ideal:
```text
/home/<svc>/<svc>-hub/README.md
```

---

## 7) Updates + change control

### 7.1 Update cadence
- patch images regularly
- avoid “latest” tags for critical components (pin versions)

### 7.2 Controlled changes
When making risky changes:
- snapshot VM/LXC first (your baseline backups already cover most cases)
- change one variable at a time
- record the change in the service README

---

## 8) Observability minimums

At minimum, you should be able to answer:
- Is it up?
- Is it healthy?
- What changed?
- Where are the logs?
- What’s eating CPU/RAM/disk?

Practical baseline:
- container healthchecks
- docker local log rotation enabled host-wide
- a “top-level” dashboard later (optional)

---

## 9) Decommission standard (clean exits)

When removing a service:
- stop compose
- archive hub (config + data) before deleting
- remove DNS/proxy routes
- remove firewall rules
- remove service account if truly dead

---

## 10) “Cool next” upgrades

- standardize on one metrics agent across all hosts
- add lightweight alerting (uptime checks)
- enforce “version pinning” checks in CI (optional, not required)
