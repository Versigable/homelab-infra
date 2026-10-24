#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# deploy.sh (Navidrome) — Model B GitOps
#
# Deploys Navidrome to /home/navidrome/navidrome-hub on LXC 114. There is no manual
# bootstrap: the LXC, its runner, the runner's sudoers, the SOPS age key, the directory
# layout and the read-only music mount all come from iac/ (Terraform + Ansible).
#
# Usage: deploy.sh [navidrome]
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

TARGET="${1:-navidrome}"
[[ "$TARGET" == "navidrome" ]] || die "Invalid target: '$TARGET'. Only 'navidrome' is valid."

# -------- Paths --------
REPO_DIR="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-/etc/sops/age/keys.txt}"
export SOPS_AGE_KEY_FILE

SERVICE_USER="navidrome"
LIVE_HUB="/home/navidrome/navidrome-hub"
LIVE_COMPOSE_DIR="${LIVE_HUB}/compose"
LIVE_DATA_DIR="${LIVE_HUB}/data"
MUSIC_DIR="/srv/kannis-music"
ND_URL="http://127.0.0.1:4533"

REPO_COMPOSE="${REPO_DIR}/hosts/navidrome/compose/navidrome.yml"
REPO_SOPS_ENV="${REPO_DIR}/hosts/navidrome/secrets/navidrome.env.sops"

# -------- Pre-flight --------
require_cmd sops
require_cmd docker
require_cmd sudo
require_cmd curl
require_cmd findmnt
sudo -n /usr/bin/docker ps >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/docker"
sudo -n /usr/bin/install --version >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/install"
[[ -r "${SOPS_AGE_KEY_FILE}" ]] || die "sops age key not readable: ${SOPS_AGE_KEY_FILE}"
for f in "${REPO_COMPOSE}" "${REPO_SOPS_ENV}"; do
  [[ -f "$f" ]] || die "missing: $f"
done
[[ -d "${LIVE_HUB}" ]] || die "missing live hub: ${LIVE_HUB} (run iac/ansible site.yml)"
# A container started without the music mount sees an empty folder, and a scan then
# drops the whole library from the database. The hookscript should prevent that; this
# catches a mount that went away after boot.
findmnt -n "${MUSIC_DIR}" >/dev/null || die "${MUSIC_DIR} is not mounted (bind mount from phoenix's /mnt/kannis-music)"

git -C "$REPO_DIR" show --name-only --pretty="format:%h %s" -1 || true
echo

compose() { (cd "${LIVE_COMPOSE_DIR}" && sudo /usr/bin/docker compose "$@"); }

# -------- Secrets + compose --------
# .env holds the decrypted secret plus the service user's ids for the compose file.
render_env() {
  log_info "Decrypting secrets -> .env"
  (
    umask 077
    tmp=$(mktemp)
    trap 'rm -f "${tmp}"' EXIT
    sops -d --input-type dotenv --output-type dotenv "${REPO_SOPS_ENV}" > "${tmp}"
    grep -qE '^ND_PASSWORDENCRYPTIONKEY=.' "${tmp}" || die "decrypted env is missing ND_PASSWORDENCRYPTIONKEY"
    printf 'NAVIDROME_UID=%s\nNAVIDROME_GID=%s\n' "$(id -u "${SERVICE_USER}")" "$(id -g "${SERVICE_USER}")" >> "${tmp}"
    sudo /usr/bin/install -m 0600 -o root -g root "${tmp}" "${LIVE_COMPOSE_DIR}/.env"
  )
}

sync_compose() {
  log_info "Syncing the compose file and pulling images"
  sudo /usr/bin/install -m 0644 -o root -g root "${REPO_COMPOSE}" "${LIVE_COMPOSE_DIR}/docker-compose.yml"
  compose pull --quiet
}

compose_up() {
  log_info "docker compose up -d"
  # The project has its own name (name: navidrome), so --remove-orphans only touches
  # this stack.
  compose up -d --remove-orphans
  compose ps
}

# -------- Checks --------
wait_ready() {
  log_info "Waiting for Navidrome on ${ND_URL} (up to 2 min)"
  local i
  for i in $(seq 1 24); do
    if [[ "$(curl -s -o /dev/null -w '%{http_code}' -m 5 "${ND_URL}/ping" || true)" == 200 ]]; then
      log_success "Navidrome is up"
      return 0
    fi
    sleep 5
  done
  sudo /usr/bin/docker logs --tail 40 navidrome 2>&1 || true
  die "Navidrome did not answer within 2 minutes"
}

log_info "========================================"
log_info "Navidrome deployment"
log_info "========================================"
render_env
sync_compose
compose_up
wait_ready
log_success "Navidrome deployed"
# The first visitor to a new Navidrome creates its admin account, so the public route
# (hosts/traefik navidrome.yml) is merged only after the admin exists.
log_info "First deploy? Create the admin at http://10.0.1.14:4533 before the public route goes live."
