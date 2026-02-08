#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# deploy.sh (ServiceHub - Multi-Service) — Model B GitOps
#
# Usage:
#   deploy.sh          — Deploy ALL services (manual full redeploy)
#   deploy.sh wiki     — Deploy only Wiki.js
#   deploy.sh n8n      — Deploy only N8N
#
# GitOps manages:
#   - compose files: hosts/servicehub/compose/*.yml -> /home/*/compose/docker-compose.yml
#   - env secrets:   hosts/servicehub/secrets/*.env.sops -> /home/*/compose/.env
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

# -------- Target selection --------
TARGET="${1:-all}"

VALID_TARGETS="all wiki n8n"
if ! echo "$VALID_TARGETS" | grep -qw "$TARGET"; then
  die "Invalid target: '$TARGET'. Valid targets: ${VALID_TARGETS}"
fi

REPO_DIR="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
log_info "REPO_DIR=${REPO_DIR}"
log_info "TARGET=${TARGET}"

SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-/etc/sops/age/keys.txt}"
export SOPS_AGE_KEY_FILE

# -------- Pre-flight checks --------
require_cmd sops
require_cmd docker
require_cmd sudo

sudo -n /usr/bin/docker ps >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/docker"
sudo -n /usr/bin/install --version >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/install"
[[ -f "${SOPS_AGE_KEY_FILE}" ]] || die "SOPS age key not found: ${SOPS_AGE_KEY_FILE}"
[[ -r "${SOPS_AGE_KEY_FILE}" ]] || die "SOPS age key not readable: ${SOPS_AGE_KEY_FILE}"

log_info "Git changes (latest commit):"
git -C "$REPO_DIR" show --name-only --pretty="format:%h %s" -1 || true
log_info ""

# -------- Compose sync --------
sync_compose() {
  local repo_file="$1"
  local live_dir="$2"
  local src="${REPO_DIR}/${repo_file}"
  local dst="${live_dir}/docker-compose.yml"

  [[ -f "$src" ]] || die "missing repo compose: ${repo_file}"
  [[ -d "$live_dir" ]] || die "missing live dir: ${live_dir}"

  log_info "Syncing ${repo_file} -> ${dst}"
  sudo /usr/bin/install -m 0644 -o root -g root "$src" "$dst"
}

# -------- Deploy a single service --------
deploy_stack() {
  local name="$1"
  local sops_env_rel="$2"
  local live_stack_dir="$3"

  local sops_env_file="${REPO_DIR}/${sops_env_rel}"
  local live_env_file="${live_stack_dir}/.env"

  log_info "========================================"
  log_info "Deploying: ${name}"
  log_info "Stack:     ${live_stack_dir}"
  log_info "========================================"

  [[ -f "${sops_env_file}" ]] || die "missing encrypted env: ${sops_env_file}"
  [[ -d "${live_stack_dir}" ]] || die "missing stack dir: ${live_stack_dir}"

  (
    umask 077
    local tmp_env
    tmp_env="$(mktemp)"
    trap 'rm -f "${tmp_env}"' EXIT

    log_info "Decrypting secrets for ${name}..."
    set +x
    sops -d --input-type dotenv --output-type dotenv "${sops_env_file}" > "${tmp_env}"
    grep -qE '^[A-Za-z_][A-Za-z0-9_]*=' "${tmp_env}" || die "decrypted env empty/invalid for ${name}"

    sudo /usr/bin/install -m 0600 -o root -g root "${tmp_env}" "${live_env_file}"
  )

  (
    cd "${live_stack_dir}"
    sudo /usr/bin/docker compose pull
    sudo /usr/bin/docker compose up -d
    sudo /usr/bin/docker compose ps
  )

  log_success "✅ ${name} deployed successfully"
  echo ""
}

# -------- Targeted deploy logic --------
FAILED=()

deploy_if_target() {
  local key="$1" label="$2" compose_repo="$3" secrets_repo="$4" live_dir="$5"

  if [[ "$TARGET" == "all" || "$TARGET" == "$key" ]]; then
    sync_compose "$compose_repo" "$live_dir"
    if ! deploy_stack "$label" "$secrets_repo" "$live_dir"; then
      FAILED+=("$key")
    fi
  fi
}

# ============================================================
# Main
# ============================================================
log_info "========================================"
log_info "ServiceHub Multi-Service Deployment"
log_info "Target: ${TARGET}"
log_info "========================================"
log_info ""

deploy_if_target "wiki" "Wiki.js" \
  "hosts/servicehub/compose/wiki.yml" \
  "hosts/servicehub/secrets/wiki.env.sops" \
  "/home/wiki/wiki-hub/compose"

deploy_if_target "n8n" "N8N" \
  "hosts/servicehub/compose/n8n.yml" \
  "hosts/servicehub/secrets/n8n.env.sops" \
  "/home/n8n/n8n-hub/compose"

# ============================================================
# Summary
# ============================================================
log_info "========================================"
log_info "Deployment Summary (target: ${TARGET})"
log_info "========================================"

if [[ ${#FAILED[@]} -eq 0 ]]; then
  log_success "🎉 All targeted services deployed successfully!"
  exit 0
else
  log_error "❌ Failed services: ${FAILED[*]}"
  exit 1
fi
