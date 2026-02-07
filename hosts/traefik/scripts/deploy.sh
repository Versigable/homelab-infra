#!/usr/bin/env bash
set -euo pipefail
set +x

log() { echo "[deploy] $*"; }
die() { echo "[deploy] ERROR: $*" >&2; exit 1; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || die "missing command: $1"; }

require_cmd sops
require_cmd docker
require_cmd sudo

# Repo root: CI uses CI_PROJECT_DIR; local uses script-relative
REPO_DIR="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
log "REPO_DIR=${REPO_DIR}"

SOPS_ENV_FILE="${REPO_DIR}/hosts/traefik/secrets/compose.env.sops"
LIVE_STACK_DIR="${LIVE_STACK_DIR:-/home/traefik/traefik-hub/compose}"
LIVE_ENV_FILE="${LIVE_ENV_FILE:-${LIVE_STACK_DIR}/.env}"
SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-/etc/sops/age/keys.txt}"
export SOPS_AGE_KEY_FILE

sudo -n /usr/bin/docker ps >/dev/null || die "runner needs NOPASSWD sudo for /usr/bin/docker"
sudo -n /usr/bin/install --version >/dev/null || die "runner needs NOPASSWD sudo for /usr/bin/install"

# --- GitOps sync (Traefik) ---
require_cmd rsync

REPO_COMPOSE="${REPO_DIR}/hosts/traefik/compose/docker-compose.yml"
LIVE_COMPOSE="${LIVE_STACK_DIR}/docker-compose.yml"

REPO_CFG_DIR="${REPO_DIR}/hosts/traefik/config"
LIVE_CFG_DIR="/home/traefik/traefik-hub/config"

[[ -f "$REPO_COMPOSE" ]] || di:contentReference[oaicite:8]{index=8}s:contentReference[oaicite:9]{index=9}-d "$REPO_CFG_DIR"  ]] || die "missing repo config dir: $REPO_CFG_DIR"

log "Syncing compose -> live"
sudo /usr/bin/install -m 0644 -o root -g root "$REPO_COMPOSE" "$LIVE_COMPOSE"

log "Syncing config/ -> live (static+dynamic)"
sudo /usr/local/sbin/gitops-sync-traefik "$REPO_CFG_DIR" "$LIVE_CFG_DIR"

[[ -r "${SOPS_AGE_KEY_FILE}" ]] || die "age key not readable: ${SOPS_AGE_KEY_FILE}"
[[ -f "${SOPS_ENV_FILE}" ]] || die "missing encrypted env: ${SOPS_ENV_FILE}"
[[ -d "${LIVE_STACK_DIR}" ]] || die "LIVE_STACK_DIR missing: ${LIVE_STACK_DIR}"

umask 077
TMP_ENV="$(mktemp)"
trap 'rm -f "${TMP_ENV}"' EXIT

log "Decrypting -> temp env"
sops -d --input-type dotenv --output-type dotenv "${SOPS_ENV_FILE}" > "${TMP_ENV}"

grep -qE '^[A-Za-z_][A-Za-z0-9_]*=' "${TMP_ENV}" \
  || die "decrypted env empty/invalid (expected KEY=VALUE)"

log "Installing live env: ${LIVE_ENV_FILE}"
sudo /usr/bin/install -m 0600 -o root -g root "${TMP_ENV}" "${LIVE_ENV_FILE}"

log "Deploying in ${LIVE_STACK_DIR}"
cd "${LIVE_STACK_DIR}"
sudo /usr/bin/docker compose pull
sudo /usr/bin/docker compose up -d
sudo /usr/bin/docker compose ps

log "Done."
