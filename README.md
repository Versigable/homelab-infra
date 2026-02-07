# homelab-infra (NinjaPrivacy)

Single source of truth for homelab infrastructure: service layouts, host baselines, security conventions, and deployment automation.

This repo is designed for **collaboration without chaos**:
- predictable directory structure across hosts/services
- locked-down service accounts + least privilege
- secrets never committed in plaintext
- docs that reflect how the lab *actually* runs

---

## What this repo is (and isn’t)

### ✅ This repo **is**
- a canonical place for infra configs, Compose stacks, and operational docs
- a standards hub for:
  - directory structure
  - service user model
  - permissions/ACL collaboration
  - host baselines + security posture
- the place where CI/CD reads deployment intent

### ❌ This repo is **not**
- a dumping ground for random one-off scripts
- a place for plaintext secrets (ever)
- a replacement for VM/LXC backups (those stay handled at the platform layer)

---

## Quick start

### Clone (internal GitLab)
Use SSH where possible (recommended), or HTTPS if needed.

```bash
git clone <your-gitlab-ssh-url>
# or
git clone <your-gitlab-https-url>
```

### Basic workflow (safe + boring)
```bash
git checkout -b feat/<short-name>
# make changes
git add .
git commit -m "Describe the change"
git push -u origin feat/<short-name>
# open a Merge Request -> review -> merge to main
```

---

## Repo layout (high level)

> Repo is organized to scale as the lab grows.

```text
.
├─ hosts/                 # host-scoped configs, compose, scripts, secrets refs
│  ├─ traefik/
│  ├─ authentik/
│  ├─ gaminghub/
│  └─ servicehub/
├─ docs/                  # “how we run things” documentation
└─ scripts/               # shared helper scripts (portable + reviewed)
```

### Runtime layout on hosts (the “hub” contract)
Services live on hosts like:

```text
/home/<svc>/<svc>-hub/
├─ compose/
├─ config/
├─ data/
├─ logs/
└─ backups/
```

This boundary makes permissioning, recovery, and collaboration predictable.

---

## Standards (must-follow)

### 1) Service accounts
- One Linux account per service (or service family)
- Locked password + `nologin` by default
- Humans are never forced into the `docker` group
- Automation uses restricted sudo allowlists (not blanket root)

### 2) Permissions & collaboration
We use a shared `devops` group **only** inside service hubs (not across the whole host).
- service home dirs stay “stealthy” (traverse allowed, listing denied)
- hubs are setgid + default ACL so new files inherit correct perms
- secrets remain opt-out / root-only when required

### 3) Docker hygiene
- `restart: unless-stopped`
- daemon log rotation enabled everywhere
- healthchecks for anything important
- minimal published ports (prefer proxy routing patterns)

---

## CI/CD + Deployments (overview)

CI/CD is enabled, but the mechanics live in the dedicated docs.
At a high level:
- CI decides *what* should deploy
- the target host performs the deployment actions
- secrets are decrypted only where they run

> See the GitOps/CI docs index below for the full details.

---

## Documentation index

### DevOps (operations + standards)
- **DevOps Host Baseline:** `docs/devops/DEVOPS_HOST_BASELINE.md`
- **DevOps Service Standards:** `docs/devops/DEVOPS_SERVICE_STANDARDS.md`
- **DevOps Security Baseline:** `docs/devops/DEVOPS_SECURITY_BASELINE.md`
- **DevOps Operations Runbook (Boss Level):** `docs/devops/DEVOPS_OPERATIONS_RUNBOOK.md`

### GitOps / CI / Secrets (existing docs)
- GitOps playbooks and runner/CI standards live alongside the above docs.
  (Keep these as links to your existing MDs—don’t duplicate.)

> Tip: If you keep docs in `/docs`, enforce it as the canonical location so GitLab’s sidebar stays clean.

---

## Contributing (collaborator-friendly rules)

### Merge Requests
- Prefer MRs over direct pushes to `main`
- Keep changes small and host/service-scoped
- Include screenshots/log snippets in MR description when relevant

### Style rules
- Shell scripts MUST be **LF** (no CRLF)
- Avoid inline secrets in YAML
- Prefer explicit paths and predictable filenames

---

## Support / Contact

- Use GitLab Issues for:
  - bugs
  - improvements
  - “runbook gaps” (missing operational docs)
- For urgent ops: follow `docs/devops/DEVOPS_OPERATIONS_RUNBOOK.md` (triage + rollback patterns)

---

## Roadmap (living)
- Standardize dashboards/metrics across hosts
- Add automated drift checks (read-only)
- Expand per-service runbooks (10-line minimum per service)

---

## License
Private homelab repo. Licensing determined by the project owner if/when open-sourced.
