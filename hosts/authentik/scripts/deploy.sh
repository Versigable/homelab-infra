#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# deploy.sh (Authentik host - Multi-Service) — Model B GitOps
#
# GitOps manages (on this host):
#   - Authentik compose:
#       repo:  hosts/authentik/compose/docker-compose.yml
#       live:  /home/authentik/authentik-hub/compose/docker-compose.yml
#   - Authentik secrets:
#       repo:  hosts/authentik/secrets/authentik.env.sops
#       live:  /home/authentik/authentik-hub/compose/.env (root:root 0600)
#
# Bitwarden:
#   - By default we DO NOT GitOps-sync Bitwarden compose (installer-managed often).
#   - We optionally decrypt secrets to /opt/bitwarden/bwdata/docker/.env if present.
#   - We will restart the Bitwarden compose stack if the directory exists.
#
# Requirements:
#   - host age key: /etc/sops/age/keys.txt readable by sops (via runner perms)
#   - gitlab-runner has NOPASSWD sudo for:
#       /usr/bin/docker, /usr/bin/install  (and optionally wrapper if you add it later)
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

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing command: $1"
}

# Repo root: CI uses CI_PROJECT_DIR; local uses script-relative
REPO_DIR="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
log_info "REPO_DIR=${REPO_DIR}"

# Age key location on the host
SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-/etc/sops/age/keys.txt}"
export SOPS_AGE_KEY_FILE

# Live paths
AUTH_LIVE_STACK_DIR="/home/authentik/authentik-hub/compose"
AUTH_LIVE_ENV_FILE="${AUTH_LIVE_STACK_DIR}/.env"
AUTH_REPO_COMPOSE="${REPO_DIR}/hosts/authentik/compose/authentik.yml"
AUTH_REPO_SECRETS="${REPO_DIR}/hosts/authentik/secrets/authentik.env.sops"

BW_LIVE_STACK_DIR="/opt/bitwarden/bwdata/docker"
BW_LIVE_ENV_FILE="${BW_LIVE_STACK_DIR}/.env"
BW_REPO_SECRETS="${REPO_DIR}/hosts/authentik/secrets/bitwarden.env.sops"

# -------- Pre-flight checks --------
require_cmd sops
require_cmd docker
require_cmd sudo

sudo -n /usr/bin/docker ps >/dev/null 2>&1 || die "gitlab-runner needs NOPASSWD sudo for /usr/bin/docker"
sudo -n /usr/bin/install --version >/dev/null 2>&1 || die "gitlab-runner needs NOPASSWD sudo for /usr/bin/install"

[[ -f "${SOPS_AGE_KEY_FILE}" ]] || die "SOPS age key not found: ${SOPS_AGE_KEY_FILE}"
[[ -r "${SOPS_AGE_KEY_FILE}" ]] || die "SOPS age key not readable: ${SOPS_AGE_KEY_FILE}"

log_info "Git changes (latest commit):"
git -C "$REPO_DIR" show --name-only --pretty="format:%h %s" -1 || true
log_info ""

# ============================================================
# GitOps sync: Authentik compose -> live
# ============================================================
sync_authentik_compose() {
  log_info "Syncing Authentik compose -> live"
  [[ -f "${AUTH_REPO_COMPOSE}" ]] || die "missing repo compose: ${AUTH_REPO_COMPOSE}"
  [[ -d "${AUTH_LIVE_STACK_DIR}" ]] || die "missing live dir: ${AUTH_LIVE_STACK_DIR}"

  sudo /usr/bin/install -m 0644 -o root -g root "${AUTH_REPO_COMPOSE}" \
    "${AUTH_LIVE_STACK_DIR}/docker-compose.yml"
}

# ============================================================
# Decrypt a dotenv secret to a live .env (safe temp + cleanup)
# ============================================================
decrypt_env_to_live() {
  local sops_file="$1"
  local live_env="$2"
  local label="$3"

  [[ -f "$sops_file" ]] || die "missing encrypted env: $sops_file"

  (
    umask 077
    TMP_ENV="$(mktemp)"
    trap 'rm -f "${TMP_ENV}"' EXIT

    log_info "Decrypting secrets for ${label}..."
    set +x
    sops -d --input-type dotenv --output-type dotenv "$sops_file" > "${TMP_ENV}"

    if ! grep -qE '^[A-Za-z_][A-Za-z0-9_]*=' "${TMP_ENV}"; then
      log_warn "Decrypted env for ${label} looks empty/odd (expected KEY=VALUE). Continuing anyway."
    fi

    log_info "Installing .env -> ${live_env}"
    sudo /usr/bin/install -m 0600 -o root -g root "${TMP_ENV}" "${live_env}"
  )
}

# ============================================================
# Deploy Authentik
# ============================================================
deploy_authentik() {
  log_info "========================================"
  log_info "Deploying: Authentik"
  log_info "Stack:     ${AUTH_LIVE_STACK_DIR}"
  log_info "========================================"

  sync_authentik_compose

  [[ -f "${AUTH_REPO_SECRETS}" ]] || die "missing secrets file: ${AUTH_REPO_SECRETS}"
  [[ -d "${AUTH_LIVE_STACK_DIR}" ]] || die "missing live dir: ${AUTH_LIVE_STACK_DIR}"

  decrypt_env_to_live "${AUTH_REPO_SECRETS}" "${AUTH_LIVE_ENV_FILE}" "Authentik"

  log_info "Deploying Authentik containers..."
  (
    cd "${AUTH_LIVE_STACK_DIR}"
    sudo /usr/bin/docker compose pull
    sudo /usr/bin/docker compose up -d
    sudo /usr/bin/docker compose ps
  )

  log_success "✅ Authentik deployed successfully"
  echo ""
}

# ============================================================
# Deploy / Restart Bitwarden (optional secrets)
# ============================================================
deploy_bitwarden() {
  log_info "========================================"
  log_info "Deploying: Bitwarden (restart)"
  log_info "Stack:     ${BW_LIVE_STACK_DIR}"
  log_info "========================================"

  if [[ ! -d "${BW_LIVE_STACK_DIR}" ]]; then
    log_warn "Bitwarden stack dir not found: ${BW_LIVE_STACK_DIR}"
    log_warn "Skipping Bitwarden."
    echo ""
    return 0
  fi

  # Optional secrets injection
  if [[ -f "${BW_REPO_SECRETS}" ]]; then
    decrypt_env_to_live "${BW_REPO_SECRETS}" "${BW_LIVE_ENV_FILE}" "Bitwarden"
  else
    log_warn "No Bitwarden secrets file found: ${BW_REPO_SECRETS} (OK if installer-managed)"
  fi

  log_info "Restarting Bitwarden containers..."
  (
    cd "${BW_LIVE_STACK_DIR}"
    sudo /usr/bin/docker compose pull
    sudo /usr/bin/docker compose up -d
    sudo /usr/bin/docker compose ps
  )

  log_success "✅ Bitwarden deployed/restarted"
  echo ""
}

# ============================================================
# Main
# ============================================================
FAILED=()

log_info "========================================"
log_info "Authentik Host Multi-Service Deployment"
log_info "========================================"
log_info "Services: Authentik, Bitwarden"
log_info ""

if ! deploy_authentik; then
  log_error "❌ Failed: Authentik"
  FAILED+=("authentik")
fi

if ! deploy_bitwarden; then
  log_error "❌ Failed: Bitwarden"
  FAILED+=("bitwarden")
fi

log_info "========================================"
log_info "Deployment Summary"
log_info "========================================"

if [[ ${#FAILED[@]} -eq 0 ]]; then
  log_success "🎉 All services deployed successfully!"
  exit 0
else
  log_error "❌ Failed services: ${FAILED[*]}"
  exit 1
fi
