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
