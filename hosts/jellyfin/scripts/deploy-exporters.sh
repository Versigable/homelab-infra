#!/usr/bin/env bash
set -euo pipefail

# deploy-exporters.sh (Jellyfin Host) — Model B GitOps
#
# Deploys the metrics exporters the Wolverine Prometheus scrapes on jf
# (compose/exporters.yml: cAdvisor and nvidia_gpu_exporter). node_exporter is the
# apt package, not part of this stack.
#
# Kept apart from deploy.sh for the same reason as deploy-webstation.sh: CI
# re-runs deploy-arr and deploy-jellyfin on any deploy.sh change.

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info()    { echo -e "${BLUE}[deploy]${NC} $*"; }
log_success() { echo -e "${GREEN}[deploy]${NC} $*"; }
log_error()   { echo -e "${RED}[deploy]${NC} $*"; }
die() { log_error "ERROR: $*"; exit 1; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || die "missing command: $1"; }

REPO_DIR="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"

STACK_DIR="/home/jf/exporters-hub"
LIVE_COMPOSE_DIR="${STACK_DIR}/compose"
SRC_COMPOSE="${REPO_DIR}/hosts/jellyfin/compose/exporters.yml"

require_cmd docker
require_cmd sudo

sudo -n /usr/bin/docker ps >/dev/null 2>&1        || die "runner needs NOPASSWD sudo for /usr/bin/docker"
sudo -n /usr/bin/install --version >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/install"

log_info "Git changes:"
git -C "$REPO_DIR" show --name-only --pretty="format:%h %s" -1 || true

[[ -f "$SRC_COMPOSE" ]]      || die "missing compose: $SRC_COMPOSE"
[[ -d "$LIVE_COMPOSE_DIR" ]] || die "missing stack dir: $LIVE_COMPOSE_DIR"

log_info "Syncing compose -> ${LIVE_COMPOSE_DIR}/docker-compose.yml"
sudo /usr/bin/install -m 0644 -o root -g root "$SRC_COMPOSE" "${LIVE_COMPOSE_DIR}/docker-compose.yml"

log_info "Deploying exporters stack..."
(
  cd "$LIVE_COMPOSE_DIR"
  sudo /usr/bin/docker compose pull
  sudo /usr/bin/docker compose up -d --remove-orphans
  sudo /usr/bin/docker compose ps
)

log_success "exporters stack deployed"
