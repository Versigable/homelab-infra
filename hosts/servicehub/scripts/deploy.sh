#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------------
# deploy.sh (servicehub host)
#
# Runs on: servicehub host (runner-per-host, shell executor)
# Purpose:
#   - Decrypt SOPS secret env file from repo checkout
#   - Write .env into the live stack directory on the host
#   - Deploy (docker compose up -d)
# ------------------------------------------------------------

# Where the repo is checked out (in CI this is the job workspace)
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
echo "[deploy] REPO_DIR=${REPO_DIR}"

# Encrypted env file in repo (relative + absolute)
SOPS_ENV_REL="hosts/servicehub/secrets/compose.env.sops"
SOPS_ENV_FILE="${REPO_DIR}/${SOPS_ENV_REL}"

# Live stack directory on the servicehub host
LIVE_STACK_DIR="${LIVE_STACK_DIR:-/home/servicehub/servicehub-hub/compose}"

# Destination .env used by docker compose in LIVE_STACK_DIR
LIVE_ENV_FILE="${LIVE_ENV_FILE:-${LIVE_STACK_DIR}/.env}"

# Age key location on the host
SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-/etc/sops/age/keys.txt}"

log() { echo "[deploy] $*"; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "[deploy] ERROR: missing command: $1" >&2; exit 1; }; }

# Never echo secrets even if job enables xtrace
set +x

require_cmd sops
require_cmd docker
require_cmd sudo

# Validate sudo permissions for exactly what we need (runner should have NOPASSWD for these)
sudo -n /usr/bin/docker ps >/dev/null
sudo -n /usr/bin/install --version >/dev/null

# Ensure age key exists + is readable
if [ ! -f "${SOPS_AGE_KEY_FILE}" ]; then
  echo "[deploy] ERROR: SOPS age key not found at ${SOPS_AGE_KEY_FILE}" >&2
  exit 1
fi
if [ ! -r "${SOPS_AGE_KEY_FILE}" ]; then
  echo "[deploy] ERROR: SOPS age key exists but is not readable: ${SOPS_AGE_KEY_FILE}" >&2
  exit 1
fi
export SOPS_AGE_KEY_FILE

# Ensure encrypted env exists in repo checkout
if [ ! -f "${SOPS_ENV_FILE}" ]; then
  echo "[deploy] ERROR: Encrypted env file not found: ${SOPS_ENV_FILE}" >&2
  exit 1
fi

# Ensure live stack dir exists (runner must be able to traverse it to cd)
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
# IMPORTANT: force output type, otherwise SOPS may try binary output and fail
sops -d --input-type dotenv --output-type dotenv "${SOPS_ENV_FILE}" > "${TMP_ENV}"

# Sanity check: KEY=VALUE lines exist
if ! grep -qE '^[A-Za-z_][A-Za-z0-9_]*=' "${TMP_ENV}"; then
  echo "[deploy] ERROR: Decrypted env does not look like KEY=VALUE content (or is empty)." >&2
  exit 1
fi

# Install to destination with root-only perms (secrets live on host, not in repo)
log "Writing live env: ${LIVE_ENV_FILE}"
sudo /usr/bin/install -m 0600 -o root -g root "${TMP_ENV}" "${LIVE_ENV_FILE}"

# Deploy stack
log "Deploying stack in ${LIVE_STACK_DIR}"
cd "${LIVE_STACK_DIR}"

sudo /usr/bin/docker compose pull
sudo /usr/bin/docker compose up -d
sudo /usr/bin/docker compose ps

log "Done."
