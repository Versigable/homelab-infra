#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# deploy.sh (ServiceHub - Multi-Service) — Model B GitOps
#
# Usage:
#   deploy.sh          — Deploy ALL services (manual full redeploy)
#   deploy.sh wiki     — Deploy only Wiki.js
#   deploy.sh n8n      — Deploy only N8N
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

VALID_TARGETS="all wiki n8n romm browsergames couchdb"
if ! echo "$VALID_TARGETS" | grep -qw "$TARGET"; then
  die "Invalid target: '$TARGET'. Valid targets: ${VALID_TARGETS}"
fi

REPO_DIR="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
log_info "REPO_DIR=${REPO_DIR}"
log_info "TARGET=${TARGET}"

SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-/etc/sops/age/keys.txt}"
export SOPS_AGE_KEY_FILE

# Stable on-host install path for repo scripts the deploy needs to run as root.
# Synced from ${REPO_DIR}/scripts/ on every CI run by sync_scripts() below.
SCRIPTS_INSTALL_DIR="${SCRIPTS_INSTALL_DIR:-/opt/homelab-infra/scripts}"
DEVOPS_GRANT="${SCRIPTS_INSTALL_DIR}/devops-grant.sh"

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

# -------- Sync repo scripts/ to a stable on-host install path --------
# Lets sudoers grant a fixed path (no wildcards), and removes the dependency
# on /home/metaversig/git/... which gitlab-runner can't traverse.
sync_scripts() {
  local src_dir="${REPO_DIR}/scripts"
  local dst_dir="${SCRIPTS_INSTALL_DIR}"

  if [[ ! -d "$src_dir" ]]; then
    log_warn "no scripts/ dir in repo at ${src_dir} — skipping script sync"
    return 0
  fi

  log_info "Syncing scripts/ -> ${dst_dir}"
  sudo /bin/mkdir -p "${dst_dir}"

  shopt -s nullglob
  for script in "${src_dir}"/*.sh; do
    sudo /usr/bin/install -m 0755 -o root -g root "$script" "${dst_dir}/$(basename "$script")"
  done
  shopt -u nullglob
}

# -------- Self-heal runner ACLs on a stack's home/hub/live dirs --------
# Idempotent. Replaces the manual scripts/runner-perms.sh step on first deploy
# of a new ServiceHub service, as long as the service user + dirs already exist.
ensure_runner_acls() {
  local live_dir="$1"
  local hub_dir="$2"
  local home_dir="$3"

  # rx on home + hub for traversal; rwx (with default ACL) on the live dir for compose sync.
  # Silent on failure (e.g., dir doesn't exist yet) — sync_compose will surface the real error.
  sudo /usr/bin/setfacl -m "u:gitlab-runner:rx" "$home_dir" 2>/dev/null || true
  sudo /usr/bin/setfacl -m "u:gitlab-runner:rx" "$hub_dir"  2>/dev/null || true
  sudo /usr/bin/setfacl -m "u:gitlab-runner:rwx" "$live_dir" 2>/dev/null || true
  sudo /usr/bin/setfacl -d -m "u:gitlab-runner:rwx" "$live_dir" 2>/dev/null || true
}

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

# -------- Fix perms for devops group --------
fix_perms() {
  local hub_dir="$1"
  local home_dir="$2"

  if [[ -x "${DEVOPS_GRANT}" ]]; then
    log_info "Fixing devops perms: ${hub_dir}"
    sudo "${DEVOPS_GRANT}" "${hub_dir}" "${home_dir}"
  else
    log_warn "devops-grant.sh not found at ${DEVOPS_GRANT} — skipping perm fix"
  fi
}

# -------- Deploy a single service --------
deploy_stack() {
  local name="$1"
  local sops_env_rel="$2"
  local live_stack_dir="$3"
  local hub_dir="$4"
  local home_dir="$5"

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

  fix_perms "${hub_dir}" "${home_dir}"

  log_success "✅ ${name} deployed successfully"
  echo ""
}

# -------- Targeted deploy logic --------
FAILED=()

deploy_if_target() {
  local key="$1" label="$2" compose_repo="$3" secrets_repo="$4" live_dir="$5" hub_dir="$6" home_dir="$7"

  if [[ "$TARGET" == "all" || "$TARGET" == "$key" ]]; then
    ensure_runner_acls "$live_dir" "$hub_dir" "$home_dir"
    sync_compose "$compose_repo" "$live_dir"
    if ! deploy_stack "$label" "$secrets_repo" "$live_dir" "$hub_dir" "$home_dir"; then
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

sync_scripts

deploy_if_target "wiki" "Wiki.js" \
  "hosts/servicehub/compose/wiki.yml" \
  "hosts/servicehub/secrets/wiki.env.sops" \
  "/home/wiki/wiki-hub/compose" \
  "/home/wiki/wiki-hub" \
  "/home/wiki"

deploy_if_target "n8n" "N8N" \
  "hosts/servicehub/compose/n8n.yml" \
  "hosts/servicehub/secrets/n8n.env.sops" \
  "/home/n8n/n8n-hub/compose" \
  "/home/n8n/n8n-hub" \
  "/home/n8n"


deploy_if_target "romm" "RomM" \
  "hosts/servicehub/compose/romm.yml" \
  "hosts/servicehub/secrets/romm.env.sops" \
  "/home/romm/romm-hub/compose" \
  "/home/romm/romm-hub" \
  "/home/romm"

deploy_if_target "browsergames" "Browser Games" \
  "hosts/servicehub/compose/browsergames.yml" \
  "hosts/servicehub/secrets/browsergames.env.sops" \
  "/home/browsergames/browsergames-hub/compose" \
  "/home/browsergames/browsergames-hub" \
  "/home/browsergames"

deploy_if_target "couchdb" "CouchDB" \
  "hosts/servicehub/compose/couchdb.yml" \
  "hosts/servicehub/secrets/couchdb.env.sops" \
  "/home/couchdb/couchdb-hub/compose" \
  "/home/couchdb/couchdb-hub" \
  "/home/couchdb"

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
