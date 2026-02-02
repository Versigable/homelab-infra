#!/usr/bin/env bash
set -euo pipefail
set +x

log() { echo "[deploy] $*"; }
warn() { echo "[deploy] WARN: $*" >&2; }
die() { echo "[deploy] ERROR: $*" >&2; exit 1; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || die "missing command: $1"; }

require_cmd sops
require_cmd docker
require_cmd sudo

REPO_DIR="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
log "REPO_DIR=${REPO_DIR}"

SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-/etc/sops/age/keys.txt}"
export SOPS_AGE_KEY_FILE

sudo -n /usr/bin/docker ps >/dev/null || die "runner needs NOPASSWD sudo for /usr/bin/docker"
sudo -n /usr/bin/install --version >/dev/null || die "runner needs NOPASSWD sudo for /usr/bin/install"
[[ -r "${SOPS_AGE_KEY_FILE}" ]] || die "age key not readable: ${SOPS_AGE_KEY_FILE}"

deploy_stack() {
  local name="$1"
  local sops_env_rel="$2"
  local live_stack_dir="$3"

  local sops_env_file="${REPO_DIR}/${sops_env_rel}"
  local live_env_file="${live_stack_dir}/.env"

  log "----------------------------------------"
  log "Deploying: ${name}"
  log "Secrets:   ${sops_env_rel}"
  log "Stack:     ${live_stack_dir}"

  [[ -f "${sops_env_file}" ]] || die "missing encrypted env: ${sops_env_file}"
  [[ -d "${live_stack_dir}" ]] || die "missing stack dir: ${live_stack_dir}"

  umask 077
  local tmp_env
  tmp_env="$(mktemp)"
  trap 'rm -f "${tmp_env}"' RETURN

  sops -d --input-type dotenv --output-type dotenv "${sops_env_file}" > "${tmp_env}"
  grep -qE '^[A-Za-z_][A-Za-z0-9_]*=' "${tmp_env}" || die "decrypted env empty/invalid for ${name}"

  sudo /usr/bin/install -m 0600 -o root -g root "${tmp_env}" "${live_env_file}"

  cd "${live_stack_dir}"
  sudo /usr/bin/docker compose pull
  sudo /usr/bin/docker compose up -d
  sudo /usr/bin/docker compose ps

  log "OK: ${name}"
}

# ServiceHub services
deploy_stack "Wiki" "hosts/servicehub/secrets/wiki.env.sops" "/home/wiki/wiki-hub/compose"
deploy_stack "n8n"  "hosts/servicehub/secrets/n8n.env.sops"  "/home/n8n/n8n-hub/compose"

log "Done."
