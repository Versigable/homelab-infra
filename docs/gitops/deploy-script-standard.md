# Deploy Script Standard (Model B GitOps)

This doc defines the **canonical deploy.sh pattern** used in `homelab-infra` for **Model B GitOps**:

> Every deploy run enforces **repo → live sync**, decrypts secrets **on-host**, then runs `docker compose up -d`.

This pattern is verified working across **Traefik**, **ServiceHub**, **GamingHub**, and **Authentik**.

---

## What a deploy script is responsible for

A deploy script should **always** do these in order:

1. **Resolve repo root** (CI and local compatible)
2. **Preflight checks** (commands + sudo allowlist + key availability)
3. **GitOps sync** (compose and optional config)
4. **Secrets decrypt** (SOPS dotenv, safe temp file, cleanup)
5. **Install `.env`** into the live stack (`root:root 0600`)
6. **Deploy** (`pull`, `up -d`, `ps`)
7. **Exit with useful status** (green if everything is up)

---

## Repo and live filesystem conventions

### Repo
- Compose lives under:
  - `hosts/<host>/compose/*.yml`
- Config (optional) lives under:
  - `hosts/<host>/config/static/...`
  - `hosts/<host>/config/dynamic/...`
- Encrypted dotenv secrets live under:
  - `hosts/<host>/secrets/*.env.sops`

### Live host runtime
Each service stack is runtime-standardized as:

- Compose:
  - `/home/<svc>/<svc>-hub/compose/docker-compose.yml`
- Secrets:
  - `/home/<svc>/<svc>-hub/compose/.env` (plaintext; **never** committed)
- Optional config:
  - `/home/<svc>/<svc>-hub/config/...`

**Key idea:** repo compose filenames can be service-centric (`authentik.yml`, `wiki.yml`) while live stays boring (`docker-compose.yml`).

---

## Preflight: commands + sudo allowlist

Deploy scripts should require:

- `sops`
- `docker`
- `sudo`
- `/usr/bin/install`

Optional:
- `/usr/local/sbin/gitops-sync` (directory sync wrapper)

Example preflight:

```bash
require_cmd() { command -v "$1" >/dev/null 2>&1 || die "missing command: $1"; }

require_cmd sops
require_cmd docker
require_cmd sudo

sudo -n /usr/bin/docker ps >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/docker"
sudo -n /usr/bin/install --version >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/install"
```

---

## Repo root resolution (CI + local)

Always use:

```bash
REPO_DIR="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
```

---

## GitOps sync patterns

### 1) Sync a single compose file (most common)

Use `install` so perms/ownership are deterministic:

```bash
sudo /usr/bin/install -m 0644 -o root -g root "$REPO_COMPOSE" "$LIVE_STACK_DIR/docker-compose.yml"
```

### 2) Sync a directory tree (config/static+dynamic)

Do **not** allow raw `sudo rsync` from CI. Prefer a root wrapper like:

- `/usr/local/sbin/gitops-sync <src_dir> <dst_dir>`

Then call:

```bash
sudo /usr/local/sbin/gitops-sync "$REPO_CFG_DIR" "$LIVE_CFG_DIR"
```

### 3) Multi-stack hosts (GamingHub-style)

For hosts running many stacks, define a helper:

```bash
sync_compose_map() {
  local repo_yml="$1"
  local live_dir="$2"
  sudo /usr/bin/install -m 0644 -o root -g root     "${REPO_DIR}/hosts/gaminghub/compose/${repo_yml}"     "${live_dir}/docker-compose.yml"
}
```

Then map each service to its runtime compose dir.

---

## Secrets: SOPS dotenv (the correct way)

### Correct decrypt command

```bash
sops -d --input-type dotenv --output-type dotenv "$SOPS_ENV_FILE"
```

### Safe temp handling (required)

Use a **subshell** with an `EXIT` trap so you never hit:
- temp file leaks
- `set -u` unbound variable issues
- stacked trap oddities across multiple services

```bash
decrypt_env_to_live() {
  local sops_file="$1"
  local live_env="$2"
  local label="$3"

  (
    umask 077
    TMP_ENV="$(mktemp)"
    trap 'rm -f "${TMP_ENV}"' EXIT

    sops -d --input-type dotenv --output-type dotenv "$sops_file" > "$TMP_ENV"
    sudo /usr/bin/install -m 0600 -o root -g root "$TMP_ENV" "$live_env"
  )
}
```

### `.env` permissions

- owner: `root`
- group: `root`
- mode: `0600`

This prevents accidental reads by other users/processes.

---

## Deploy step (docker compose)

Always run compose *from the live stack directory*:

```bash
(
  cd "$LIVE_STACK_DIR"
  sudo /usr/bin/docker compose pull
  sudo /usr/bin/docker compose up -d
  sudo /usr/bin/docker compose ps
)
```

---

## Logging (recommended)

Minimal but effective:

```bash
log() { echo "[deploy] $*"; }
die() { echo "[deploy] ERROR: $*" >&2; exit 1; }
```

Optional: colors (nice for human scanning, not required).

Also helpful for traceability:

```bash
git -C "$REPO_DIR" show --name-only --pretty="format:%h %s" -1 || true
```

---

## Canonical skeleton (single-stack host)

```bash
#!/usr/bin/env bash
set -euo pipefail

log() { echo "[deploy] $*"; }
die() { echo "[deploy] ERROR: $*" >&2; exit 1; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || die "missing command: $1"; }

REPO_DIR="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
log "REPO_DIR=$REPO_DIR"

require_cmd sops
require_cmd docker
require_cmd sudo

sudo -n /usr/bin/docker ps >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/docker"
sudo -n /usr/bin/install --version >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/install"

export SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-/etc/sops/age/keys.txt}"
[[ -r "$SOPS_AGE_KEY_FILE" ]] || die "age key not readable: $SOPS_AGE_KEY_FILE"

LIVE_STACK_DIR="/home/<svc>/<svc>-hub/compose"
LIVE_ENV_FILE="$LIVE_STACK_DIR/.env"

REPO_COMPOSE="$REPO_DIR/hosts/<host>/compose/<service>.yml"
SOPS_ENV_FILE="$REPO_DIR/hosts/<host>/secrets/<service>.env.sops"

# 1) Sync compose -> live
sudo /usr/bin/install -m 0644 -o root -g root "$REPO_COMPOSE" "$LIVE_STACK_DIR/docker-compose.yml"

# 2) Decrypt -> temp -> install .env
(
  umask 077
  TMP_ENV="$(mktemp)"
  trap 'rm -f "${TMP_ENV}"' EXIT
  sops -d --input-type dotenv --output-type dotenv "$SOPS_ENV_FILE" > "$TMP_ENV"
  sudo /usr/bin/install -m 0600 -o root -g root "$TMP_ENV" "$LIVE_ENV_FILE"
)

# 3) Deploy
(
  cd "$LIVE_STACK_DIR"
  sudo /usr/bin/docker compose pull
  sudo /usr/bin/docker compose up -d
  sudo /usr/bin/docker compose ps
)

log "Done."
```

Replace `<host>`, `<svc>`, `<service>` accordingly.

---

## Common failure modes (and fixes)

### “`pipefail\r` invalid option”
**Cause:** CRLF line endings  
**Fix:** enforce LF with `.gitattributes`:

```
*.sh text eol=lf
```

### “SOPS dotenv error emitting binary store”
**Cause:** missing `--output-type dotenv`  
**Fix:** always include both input/output types.

### “Sorry, user gitlab-runner is not allowed to execute /usr/bin/rsync …”
**Cause:** sudoers doesn’t allow rsync  
**Fix:** use the `/usr/local/sbin/gitops-sync` wrapper and allowlist only that.

### “TMP_ENV: unbound variable”
**Cause:** `set -u` + trap referencing unset var / bad trap type  
**Fix:** subshell + `EXIT` trap like shown above.

---

## Recommendations (next improvements)

- Add a **host guard**:
  ```bash
  [[ "$(hostname)" == "<expected-hostname>" ]] || die "wrong host"
  ```
- Add a **dry-run diff** in CI (optional):
  ```bash
  git -C "$REPO_DIR" diff --name-status "$CI_COMMIT_BEFORE_SHA" "$CI_COMMIT_SHA" || true
  ```
- Create a **bootstrap script** to standardize runner + sops + sudo + wrapper install across new hosts.

