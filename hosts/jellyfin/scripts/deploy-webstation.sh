#!/usr/bin/env bash
set -euo pipefail

# deploy-webstation.sh (Jellyfin Host) — Model B GitOps
#
# Deploys the RomM emulator-streaming webstation (compose/webstation.yml) plus the
# files it mounts from config/webstation/.
#
# Kept apart from deploy.sh on purpose: CI re-runs deploy-arr and deploy-jellyfin on
# any deploy.sh change, and re-pulling arr's floating alpine/docker-cli tags can
# recreate the VPN container under qBittorrent, leaving its netns stale.

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info()    { echo -e "${BLUE}[deploy]${NC} $*"; }
log_success() { echo -e "${GREEN}[deploy]${NC} $*"; }
log_warn()    { echo -e "${YELLOW}[deploy]${NC} $*"; }
log_error()   { echo -e "${RED}[deploy]${NC} $*"; }
die() { log_error "ERROR: $*"; exit 1; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || die "missing command: $1"; }

REPO_DIR="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-/etc/sops/age/keys.txt}"
export SOPS_AGE_KEY_FILE

STACK_DIR="/home/jf/webstation-hub"
LIVE_COMPOSE_DIR="${STACK_DIR}/compose"
SRC_COMPOSE="${REPO_DIR}/hosts/jellyfin/compose/webstation.yml"
SOPS_ENV="${REPO_DIR}/hosts/jellyfin/secrets/webstation.env.sops"
SRC_FILES="${REPO_DIR}/hosts/jellyfin/config/webstation"

require_cmd docker
require_cmd sudo
require_cmd sops

sudo -n /usr/bin/docker ps >/dev/null 2>&1        || die "runner needs NOPASSWD sudo for /usr/bin/docker"
sudo -n /usr/bin/install --version >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/install"
[[ -r "${SOPS_AGE_KEY_FILE}" ]] || die "SOPS age key not readable: ${SOPS_AGE_KEY_FILE}"

log_info "Git changes:"
git -C "$REPO_DIR" show --name-only --pretty="format:%h %s" -1 || true

[[ -f "$SRC_COMPOSE" ]]      || die "missing compose: $SRC_COMPOSE"
[[ -f "$SOPS_ENV" ]]         || die "missing secrets: $SOPS_ENV"
[[ -d "$LIVE_COMPOSE_DIR" ]] || die "missing stack dir: $LIVE_COMPOSE_DIR"
[[ -d "${STACK_DIR}/config" ]] || die "missing config dir: ${STACK_DIR}/config"

log_info "Syncing compose -> ${LIVE_COMPOSE_DIR}/docker-compose.yml"
sudo /usr/bin/install -m 0644 -o root -g root "$SRC_COMPOSE" "${LIVE_COMPOSE_DIR}/docker-compose.yml"

(
  umask 077
  TMP="$(mktemp)"
  trap 'rm -f "$TMP"' EXIT
  log_info "Decrypting webstation secrets..."
  sops -d --input-type dotenv --output-type dotenv "$SOPS_ENV" > "$TMP"
  grep -qE '^BROKER_SECRET=.+' "$TMP" || die "decrypted env lacks BROKER_SECRET"
  sudo /usr/bin/install -m 0600 -o root -g root "$TMP" "${LIVE_COMPOSE_DIR}/.env"
)

# LinuxServer runs custom-cont-init.d and custom-services.d only if root owns them
# and nobody else can write them. They are read at container start, so a change
# here needs a restart even when the compose file is unchanged.
RESTART=0
sync_file() { # sync_file <src> <dest> <mode>
  if ! cmp -s "$1" "$2" 2>/dev/null; then
    sudo /usr/bin/install -m "$3" -o root -g root "$1" "$2"
    log_info "updated $2"
    RESTART=1
  fi
}
for d in custom-cont-init.d custom-services.d seed; do
  sudo /usr/bin/install -d -m 0755 -o root -g root "${STACK_DIR}/${d}"
done
sync_file "${SRC_FILES}/custom-cont-init.d/50-seed-emulators" "${STACK_DIR}/custom-cont-init.d/50-seed-emulators" 0755
sync_file "${SRC_FILES}/custom-services.d/svc-ppsspp-shm-reaper" "${STACK_DIR}/custom-services.d/svc-ppsspp-shm-reaper" 0755
sync_file "${SRC_FILES}/seed/ppsspp-controls.ini" "${STACK_DIR}/seed/ppsspp-controls.ini" 0644

log_info "Deploying webstation stack..."
(
  cd "$LIVE_COMPOSE_DIR"
  sudo /usr/bin/docker compose pull
  sudo /usr/bin/docker compose up -d --remove-orphans
  if [[ "$RESTART" == 1 ]]; then
    log_warn "Mounted init/service files changed; restarting so they take effect"
    sudo /usr/bin/docker compose restart
  fi
  sudo /usr/bin/docker compose ps
)

log_success "webstation stack deployed"
