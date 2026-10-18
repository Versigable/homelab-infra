#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# deploy.sh (Nextcloud) — Model B GitOps
#
# Deploys Nextcloud + Collabora to /home/nextcloud/nextcloud-hub on LXC 110.
# There is no manual bootstrap: the LXC, its runner, the runner's sudoers, the
# SOPS age key and the directory layout all come from iac/ (Terraform + Ansible).
#
# Refuses to run unless the TrueNAS share is mounted at /srv/nextcloud-data.
# Without it the installer would put user files on the LXC's root disk.
#
# Usage: deploy.sh [nextcloud]
# ============================================================

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
require_cmd() { command -v "$1" >/dev/null 2>&1 || die "missing command: $1"; }

TARGET="${1:-nextcloud}"
[[ "$TARGET" == "nextcloud" ]] || die "Invalid target: '$TARGET'. Only 'nextcloud' is valid."

# -------- Paths --------
REPO_DIR="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-/etc/sops/age/keys.txt}"
export SOPS_AGE_KEY_FILE

LIVE_HUB="/home/nextcloud/nextcloud-hub"
LIVE_COMPOSE_DIR="${LIVE_HUB}/compose"
LIVE_CONFIG_DIR="${LIVE_HUB}/config"
DATA_MOUNT="/srv/nextcloud-data"
STATUS_URL="http://10.0.1.10:8080/status.php"

REPO_HOST_DIR="${REPO_DIR}/hosts/nextcloud"
REPO_COMPOSE="${REPO_HOST_DIR}/compose/nextcloud.yml"
REPO_NGINX="${REPO_HOST_DIR}/config/nginx/nextcloud.conf"
REPO_HOOKS="${REPO_HOST_DIR}/config/hooks/before-starting"
REPO_SOPS_ENV="${REPO_HOST_DIR}/secrets/nextcloud.env.sops"

# Keys the compose file interpolates; deploy stops if any is missing after decrypt.
REQUIRED_KEYS=(POSTGRES_PASSWORD REDIS_PASSWORD NEXTCLOUD_ADMIN_USER NEXTCLOUD_ADMIN_PASSWORD
               COLLABORA_ADMIN_USER COLLABORA_ADMIN_PASSWORD
               NEXTCLOUD_OIDC_CLIENT_ID NEXTCLOUD_OIDC_CLIENT_SECRET)

log_info "REPO_DIR=${REPO_DIR}"

# -------- Pre-flight --------
require_cmd sops
require_cmd docker
require_cmd sudo
require_cmd curl
require_cmd mountpoint
sudo -n /usr/bin/docker ps >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/docker"
sudo -n /usr/bin/install --version >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/install"
[[ -r "${SOPS_AGE_KEY_FILE}" ]] || die "sops age key not readable: ${SOPS_AGE_KEY_FILE}"
for f in "${REPO_COMPOSE}" "${REPO_NGINX}" "${REPO_SOPS_ENV}"; do
  [[ -f "$f" ]] || die "missing: $f"
done
[[ -d "${LIVE_HUB}" ]] || die "missing live hub: ${LIVE_HUB} (run iac/ansible site.yml)"
mountpoint -q "${DATA_MOUNT}" \
  || die "${DATA_MOUNT} is not mounted. The TrueNAS share must be in place first (nextcloud_storage_ready in iac/ansible host_vars)."

git -C "$REPO_DIR" show --name-only --pretty="format:%h %s" -1 || true
echo

# -------- Self-heal runner ACLs --------
ensure_runner_acls() {
  log_info "Self-heal runner ACLs"
  sudo /usr/bin/setfacl -m "u:gitlab-runner:rx" "/home/nextcloud" "${LIVE_HUB}"
  sudo /usr/bin/setfacl -m "u:gitlab-runner:rwx" "${LIVE_COMPOSE_DIR}"
  sudo /usr/bin/setfacl -d -m "u:gitlab-runner:rwx" "${LIVE_COMPOSE_DIR}"
}

# -------- Sync compose + config --------
sync_files() {
  log_info "Syncing compose, nginx config and hooks"
  sudo /usr/bin/install -m 0644 -o root -g root "${REPO_COMPOSE}" "${LIVE_COMPOSE_DIR}/docker-compose.yml"

  local hooks_live="${LIVE_CONFIG_DIR}/hooks/before-starting"
  sudo /usr/bin/install -d -m 0755 -o root -g root "${LIVE_CONFIG_DIR}/nginx" "${LIVE_CONFIG_DIR}/hooks" "${hooks_live}"
  sudo /usr/bin/install -m 0644 -o root -g root "${REPO_NGINX}" "${LIVE_CONFIG_DIR}/nginx/nextcloud.conf"

  # Hooks must be executable, or the image skips them. The runner may delete in
  # this dir, so hooks removed from the repo are removed here too.
  sudo /usr/bin/setfacl -m "u:gitlab-runner:rx" "${LIVE_CONFIG_DIR}" "${LIVE_CONFIG_DIR}/hooks"
  sudo /usr/bin/setfacl -m "u:gitlab-runner:rwx" "${hooks_live}"
  local f live
  shopt -s nullglob
  for f in "${REPO_HOOKS}"/*.sh; do
    sudo /usr/bin/install -m 0755 -o root -g root "$f" "${hooks_live}/$(basename "$f")"
  done
  for live in "${hooks_live}"/*.sh; do
    if [[ ! -f "${REPO_HOOKS}/$(basename "$live")" ]]; then
      log_warn "removing stale hook $(basename "$live")"
      rm -f "$live"
    fi
  done
  shopt -u nullglob
}

# -------- Decrypt secrets --------
decrypt_env() {
  log_info "Decrypting secrets -> ${LIVE_COMPOSE_DIR}/.env (root 0600)"
  (
    umask 077
    local tmp_env key
    tmp_env="$(mktemp)"
    trap 'rm -f "${tmp_env}"' EXIT
    set +x
    sops -d --input-type dotenv --output-type dotenv "${REPO_SOPS_ENV}" > "${tmp_env}"
    for key in "${REQUIRED_KEYS[@]}"; do
      grep -qE "^${key}=." "${tmp_env}" || die "decrypted env is missing ${key}"
    done
    sudo /usr/bin/install -m 0600 -o root -g root "${tmp_env}" "${LIVE_COMPOSE_DIR}/.env"
  )
}

# -------- Data dirs on the LXC disk --------
ensure_data_dirs() {
  log_info "Ensuring data dirs"
  # html: app code + config.php, owned by www-data (33); nginx (101) reads it.
  sudo /usr/bin/install -d -m 0755 -o 33 -g 33 "${LIVE_HUB}/data/html"
  # postgres: the alpine image's postgres user is 70.
  sudo /usr/bin/install -d -m 0700 -o 70 -g 70 "${LIVE_HUB}/data/postgres"
}

# -------- Compose up --------
compose_up() {
  log_info "docker compose pull && up -d"
  (
    cd "${LIVE_COMPOSE_DIR}"
    sudo /usr/bin/docker compose pull --quiet
    # The project has its own name (name: nextcloud), so --remove-orphans only
    # touches this stack.
    sudo /usr/bin/docker compose up -d --remove-orphans
    sudo /usr/bin/docker compose ps
  )
}

# -------- Wait for Nextcloud --------
# The first start installs Nextcloud and the apps, which takes a few minutes.
wait_ready() {
  log_info "Waiting for ${STATUS_URL} (up to 10 min)"
  local i body
  for i in $(seq 1 120); do
    body=$(curl -fsS -m 5 -H "Host: cloud.ninjaprivacy.org" "${STATUS_URL}" 2>/dev/null || true)
    if [[ "$body" == *'"installed":true'* && "$body" == *'"maintenance":false'* ]]; then
      log_success "Nextcloud is up: ${body}"
      return 0
    fi
    sleep 5
  done
  sudo /usr/bin/docker logs --tail 40 nextcloud-app 2>&1 || true
  die "Nextcloud did not report installed:true within 10 minutes"
}

# -------- Settings hooks --------
# The image runs config/hooks/before-starting on every start of nextcloud-app. When this
# deploy didn't (re)start the container (no image or compose change), run the hooks here
# so a settings change still takes effect. Hooks only warn, because a failing hook stops
# the container; this job fails on those warnings instead, with Nextcloud still up.
apply_settings() {
  local started output hook
  started=$(date -d "$(sudo /usr/bin/docker inspect -f '{{.State.StartedAt}}' nextcloud-app)" +%s)
  if (( started >= DEPLOY_START )); then
    log_info "nextcloud-app started during this deploy, so its settings hooks already ran"
    output=$(sudo /usr/bin/docker logs --since "${DEPLOY_START}" nextcloud-app 2>&1 | grep '^==> ' || true)
  else
    log_info "Running the settings hooks in nextcloud-app"
    output=$(
      for hook in "${LIVE_CONFIG_DIR}/hooks/before-starting"/*.sh; do
        sudo /usr/bin/docker exec -u www-data nextcloud-app \
          "/docker-entrypoint-hooks.d/before-starting/$(basename "$hook")" 2>&1 || true
      done
    )
  fi
  echo "${output}" | grep -E 'WARN|hook finished' || true
  grep -q 'settings hook finished' <<<"${output}" || die "the settings hook didn't run"
  if grep -q 'WARN' <<<"${output}"; then
    die "Nextcloud is up, but the settings hook warned (above). Fix it, or rerun this job to retry."
  fi
}

log_info "========================================"
log_info "Nextcloud deployment"
log_info "========================================"
ensure_runner_acls
sync_files
decrypt_env
ensure_data_dirs
DEPLOY_START=$(date +%s)
compose_up
wait_ready
apply_settings
log_success "Nextcloud deployed"
