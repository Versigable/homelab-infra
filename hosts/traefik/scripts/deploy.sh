#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------------
# deploy.sh (Traefik host)
#
# Runs on: Traefik VM (runner-per-host)
# Purpose:
#   - Optionally update the repo checkout
#   - Decrypt SOPS secret env file from repo
#   - Write .env into the live Traefik stack directory
#   - Deploy (docker compose up -d)
# ------------------------------------------------------------

# ---- Config you may tweak ----
# Where the repo is checked out ON THE TRAEFIK HOST
REPO_DIR="${REPO_DIR:-/home/metaversig/git/homelab-infra}"

# Encrypted env file in repo
SOPS_ENV_REL="hosts/traefik/secrets/compose.env.sops"

# Where your live Traefik compose actually lives ON THE TRAEFIK HOST
# (adjust if your path differs)
LIVE_STACK_DIR="${LIVE_STACK_DIR:-/home/traefik/traefik-hub/compose}"

# The destination .env used by docker compose in LIVE_STACK_DIR
LIVE_ENV_FILE="${LIVE_ENV_FILE:-${LIVE_STACK_DIR}/.env}"

# Age key location ON THE TRAEFIK HOST
SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-/etc/sops/age/keys.txt}"

# If you want the job to git pull before deploying
DO_GIT_PULL="${DO_GIT_PULL:-1}"

# ------------------------------------------------------------

log() { echo "[deploy] $*"; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "[deploy] ERROR: missing command: $1" >&2; exit 1; }
}

# Safety: never echo secrets. Turn off xtrace if it's enabled.
set +x

require_cmd git
require_cmd sops
require_cmd docker

# Ensure age key exists (Traefik host)
if [ ! -f "${SOPS_AGE_KEY_FILE}" ]; then
  echo "[deploy] ERROR: SOPS age key not found at ${SOPS_AGE_KEY_FILE}" >&2
  exit 1
fi
export SOPS_AGE_KEY_FILE

# Ensure repo exists
if [ ! -d "${REPO_DIR}/.git" ]; then
  echo "[deploy] ERROR: REPO_DIR not a git repo: ${REPO_DIR}" >&2
  exit 1
fi

cd "${REPO_DIR}"

if [ "${DO_GIT_PULL}" = "1" ]; then
  # In CI, repo is already at the commit being built; but if you run manually, this helps.
  # We do a safe "fetch" and ensure we're on the expected ref if desired.
  log "Fetching latest (non-destructive)..."
  git fetch --all --prune >/dev/null 2>&1 || true
fi

SOPS_ENV_FILE="${REPO_DIR}/${SOPS_ENV_REL}"
if [ ! -f "${SOPS_ENV_FILE}" ]; then
  echo "[deploy] ERROR: Encrypted env file not found: ${SOPS_ENV_FILE}" >&2
  exit 1
fi

# Make sure live stack dir exists
if [ ! -d "${LIVE_STACK_DIR}" ]; then
  echo "[deploy] ERROR: LIVE_STACK_DIR does not exist: ${LIVE_STACK_DIR}" >&2
  exit 1
fi

# Decrypt to a temp file with strict perms
umask 077
TMP_ENV="$(mktemp)"
cleanup() { rm -f "${TMP_ENV}"; }
trap cleanup EXIT

log "Decrypting SOPS env -> temp file"
sops -d "${SOPS_ENV_FILE}" > "${TMP_ENV}"

# Basic sanity check: ensure it looks like KEY=VALUE lines (not perfect, but catches empty)
if ! grep -qE '^[A-Za-z_][A-Za-z0-9_]*=' "${TMP_ENV}"; then
  echo "[deploy] ERROR: Decrypted env does not look like KEY=VALUE content (or is empty)." >&2
  echo "[deploy] Refusing to deploy to avoid writing a bad .env." >&2
  exit 1
fi

# Install to destination with root-only perms
log "Writing live env: ${LIVE_ENV_FILE}"
sudo install -m 0600 -o root -g root "${TMP_ENV}" "${LIVE_ENV_FILE}"

# Deploy Traefik stack
log "Deploying Traefik stack in ${LIVE_STACK_DIR}"
cd "${LIVE_STACK_DIR}"

# Pull + up (you can remove pull if you prefer)
sudo docker compose pull
sudo docker compose up -d

log "Deploy complete. Current status:"
sudo docker compose ps

log "Done."
