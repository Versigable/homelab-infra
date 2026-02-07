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

---

## Deploy Script Example

#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# deploy.sh (GamingHub - Multi-Service) — Model B GitOps
#
# GitOps manages:
#   - compose files: hosts/gaminghub/compose/*.yml  -> /home/*/*-hub/compose/docker-compose.yml
#   - env secrets:   hosts/gaminghub/secrets/*.env.sops -> /home/*/*-hub/compose/.env (root:root 0600)
#
# Deploys:
#   - Minecraft (2 servers) → /home/minecraft/minecraft-hub/compose
#   - Valheim → /home/valheim/valheim-hub/compose
#   - Astroneer → /home/astroneer/astroneer-hub/compose
#   - Ark SE (3 maps):
#       - Island → /home/arkse/arkse-hub/compose
#       - Ragnarok → /home/arkse/arkse-hub-rag/compose
#       - Fjordur → /home/arkse/arkse-hub-fjor/compose
#   - Sons of the Forest → /home/sotf/sotf-hub/compose
#   - Palworld → /home/palworld/palworld-hub/compose
#   - Satisfactory → /home/satisfactory/satisfactory-hub/compose
#
# Notes:
#   - One host-level Age key at /etc/sops/age/keys.txt by default
#   - docker compose is executed inside each stack dir
# ============================================================

# -------- Colors / logging --------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[deploy]${NC} $*"; }
log_success() { echo -e "${GREEN}[deploy]${NC} $*"; }
log_warn()    { echo -e "${YELLOW}[deploy]${NC} $*"; }
log_error()   { echo -e "${RED}[deploy]${NC} $*"; }

die() { log_error "ERROR: $*"; exit 1; }

# -------- Helpers --------
require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing command: $1"
}

# Repo root: CI uses CI_PROJECT_DIR; local uses script-relative
REPO_DIR="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
log_info "REPO_DIR=${REPO_DIR}"

# Age key location on the host (ONE key for all services)
SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-/etc/sops/age/keys.txt}"
export SOPS_AGE_KEY_FILE

# -------- Pre-flight checks --------
require_cmd sops
require_cmd docker
require_cmd sudo

# Validate sudo permissions required by this script
sudo -n /usr/bin/docker ps >/dev/null 2>&1 || die "gitlab-runner needs NOPASSWD sudo for /usr/bin/docker"
sudo -n /usr/bin/install --version >/dev/null 2>&1 || die "gitlab-runner needs NOPASSWD sudo for /usr/bin/install"

# Ensure age key exists and is readable
[[ -f "${SOPS_AGE_KEY_FILE}" ]] || die "SOPS age key not found: ${SOPS_AGE_KEY_FILE}"
[[ -r "${SOPS_AGE_KEY_FILE}" ]] || die "SOPS age key not readable: ${SOPS_AGE_KEY_FILE}"

# -------- GitOps sync: compose file -> live --------
sync_compose_map() {
  local repo_yml="$1"   # e.g. minecraft.yml
  local live_dir="$2"   # e.g. /home/minecraft/minecraft-hub/compose
  local src="${REPO_DIR}/hosts/gaminghub/compose/${repo_yml}"
  local dst="${live_dir}/docker-compose.yml"

  [[ -f "$src" ]] || die "missing repo compose: $src"
  [[ -d "$live_dir" ]] || die "missing live dir: $live_dir"

  log_info "Syncing compose: ${repo_yml} -> ${dst}"
  sudo /usr/bin/install -m 0644 -o root -g root "$src" "$dst"
}

# Map repo file -> live directory (compose sync happens once per run)
sync_compose_map "minecraft.yml"    "/home/minecraft/minecraft-hub/compose"
sync_compose_map "valheim.yml"      "/home/valheim/valheim-hub/compose"
sync_compose_map "sotf.yml"         "/home/sotf/sotf-hub/compose"
sync_compose_map "palworld.yml"     "/home/palworld/palworld-hub/compose"
sync_compose_map "satisfactory.yml" "/home/satisfactory/satisfactory-hub/compose"
sync_compose_map "astroneer.yml"    "/home/astroneer/astroneer-hub/compose"
sync_compose_map "ark-island.yml"   "/home/arkse/arkse-hub/compose"
sync_compose_map "ark-ragnarok.yml" "/home/arkse/arkse-hub-rag/compose"
sync_compose_map "ark-fjordur.yml"  "/home/arkse/arkse-hub-fjor/compose"

log_info "Git changes (latest commit):"
git -C "$REPO_DIR" show --name-only --pretty="format:%h %s" -1 || true
log_info ""

# ============================================================
# Deploy a single game server
# ============================================================
deploy_game() {
  local GAME_NAME="$1"     # Display name (e.g., "Minecraft Servers")
  local SECRET_NAME="$2"   # Secret file stem (e.g., "minecraft", "ark-island")
  local STACK_DIR="$3"     # Full path to compose directory

  log_info "========================================"
  log_info "Deploying: ${GAME_NAME}"
  log_info "Stack:     ${STACK_DIR}"
  log_info "========================================"

  local SOPS_ENV_FILE="${REPO_DIR}/hosts/gaminghub/secrets/${SECRET_NAME}.env.sops"
  local LIVE_ENV_FILE="${STACK_DIR}/.env"

  [[ -d "${STACK_DIR}" ]] || { log_error "Stack dir missing: ${STACK_DIR}"; return 1; }

  # Secret file missing? Not fatal — you might not be using env vars yet.
  if [[ ! -f "${SOPS_ENV_FILE}" ]]; then
    log_warn "Encrypted env file not found: ${SOPS_ENV_FILE}"
    log_warn "Skipping secrets for ${GAME_NAME} (continuing deploy)"
    # Still deploy compose; game might not need env vars.
    ( cd "${STACK_DIR}" && sudo /usr/bin/docker compose pull && sudo /usr/bin/docker compose up -d && sudo /usr/bin/docker compose ps )
    log_success "✅ ${GAME_NAME} deployed (no secrets applied)"
    echo ""
    return 0
  fi

  # Decrypt to temp file with strict perms
 (
  # subshell so trap doesn't leak across games
  umask 077
  TMP_ENV="$(mktemp)"
  trap 'rm -f "${TMP_ENV}"' EXIT

  log_info "Decrypting secrets for ${GAME_NAME}..."
  set +x  # never echo secrets
  if ! sops -d --input-type dotenv --output-type dotenv "${SOPS_ENV_FILE}" > "${TMP_ENV}"; then
    log_error "Failed to decrypt ${SOPS_ENV_FILE}"
    exit 1
  fi

  if ! grep -qE '^[A-Za-z_][A-Za-z0-9_]*=' "${TMP_ENV}"; then
    log_warn "⚠️  Decrypted env is empty/invalid for ${GAME_NAME} (expected KEY=VALUE)"
    log_warn "⚠️  Continuing anyway"
  fi

  log_info "Writing .env to ${LIVE_ENV_FILE}"
  sudo /usr/bin/install -m 0600 -o root -g root "${TMP_ENV}" "${LIVE_ENV_FILE}"
 )

  log_info "Deploying containers..."
  (
    cd "${STACK_DIR}"
    sudo /usr/bin/docker compose pull
    sudo /usr/bin/docker compose up -d
    sudo /usr/bin/docker compose ps
  )

  log_success "✅ ${GAME_NAME} deployed successfully"
  echo ""
  return 0
}

# ============================================================
# Main: Deploy all game servers on this host
# ============================================================
log_info "========================================"
log_info "GamingHub Multi-Service Deployment"
log_info "========================================"
log_info ""

FAILED_GAMES=()

run_deploy() {
  local label="$1" secret="$2" dir="$3"
  if ! deploy_game "$label" "$secret" "$dir"; then
    FAILED_GAMES+=("$secret")
  fi
}

# Minecraft (2 servers in one hub)
run_deploy "Minecraft Servers"       "minecraft"    "/home/minecraft/minecraft-hub/compose"
run_deploy "Valheim"                 "valheim"      "/home/valheim/valheim-hub/compose"
run_deploy "Astroneer"               "astroneer"    "/home/astroneer/astroneer-hub/compose"

run_deploy "Ark SE - The Island"     "ark-island"   "/home/arkse/arkse-hub/compose"
run_deploy "Ark SE - Ragnarok"       "ark-ragnarok" "/home/arkse/arkse-hub-rag/compose"
run_deploy "Ark SE - Fjordur"        "ark-fjordur"  "/home/arkse/arkse-hub-fjor/compose"

run_deploy "Sons of the Forest"      "sotf"         "/home/sotf/sotf-hub/compose"
run_deploy "Palworld"                "palworld"     "/home/palworld/palworld-hub/compose"
run_deploy "Satisfactory"            "satisfactory" "/home/satisfactory/satisfactory-hub/compose"

# Summary
log_info "========================================"
log_info "Deployment Summary"
log_info "========================================"

if [[ ${#FAILED_GAMES[@]} -eq 0 ]]; then
  log_success "🎉 All game servers deployed successfully!"
  exit 0
else
  log_error "❌ Failed games: ${FAILED_GAMES[*]}"
  log_error "Scroll up for the first error in each failed section."
  exit 1
fi
