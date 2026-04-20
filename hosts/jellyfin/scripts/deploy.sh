#!/usr/bin/env bash
set -euo pipefail

# deploy.sh (Jellyfin Host — arr stack) — Model B GitOps
#
# Usage:
#   deploy.sh        — Deploy arr stack
#   deploy.sh arr    — Deploy arr stack (explicit)

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info()    { echo -e "${BLUE}[deploy]${NC} $*"; }
log_success() { echo -e "${GREEN}[deploy]${NC} $*"; }
log_warn()    { echo -e "${YELLOW}[deploy]${NC} $*"; }
log_error()   { echo -e "${RED}[deploy]${NC} $*"; }
die() { log_error "ERROR: $*"; exit 1; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || die "missing command: $1"; }

TARGET="${1:-arr}"
VALID_TARGETS="arr"
echo "$VALID_TARGETS" | grep -qw "$TARGET" || die "Invalid target: '$TARGET'. Valid: ${VALID_TARGETS}"

REPO_DIR="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-/etc/sops/age/keys.txt}"
export SOPS_AGE_KEY_FILE

require_cmd sops
require_cmd docker
require_cmd sudo

sudo -n /usr/bin/docker ps >/dev/null 2>&1        || die "runner needs NOPASSWD sudo for /usr/bin/docker"
sudo -n /usr/bin/install --version >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/install"
[[ -f "${SOPS_AGE_KEY_FILE}" ]] || die "SOPS age key not found: ${SOPS_AGE_KEY_FILE}"
[[ -r "${SOPS_AGE_KEY_FILE}" ]] || die "SOPS age key not readable: ${SOPS_AGE_KEY_FILE}"

log_info "Git changes:"
git -C "$REPO_DIR" show --name-only --pretty="format:%h %s" -1 || true

LIVE_COMPOSE_DIR="/home/jf/arr-hub/compose"
SRC_COMPOSE="${REPO_DIR}/hosts/jellyfin/compose/arr.yml"
SOPS_ENV="${REPO_DIR}/hosts/jellyfin/secrets/arr.env.sops"
LIVE_ENV="${LIVE_COMPOSE_DIR}/.env"

[[ -f "$SRC_COMPOSE" ]]      || die "missing compose: $SRC_COMPOSE"
[[ -d "$LIVE_COMPOSE_DIR" ]] || die "missing stack dir: $LIVE_COMPOSE_DIR"
[[ -f "$SOPS_ENV" ]]         || die "missing secrets: $SOPS_ENV"

log_info "Syncing compose -> ${LIVE_COMPOSE_DIR}/docker-compose.yml"
sudo /usr/bin/install -m 0644 -o root -g root "$SRC_COMPOSE" "${LIVE_COMPOSE_DIR}/docker-compose.yml"

(
  umask 077
  TMP="$(mktemp)"
  trap 'rm -f "$TMP"' EXIT

  log_info "Decrypting arr secrets..."
  sops -d --input-type dotenv --output-type dotenv "$SOPS_ENV" > "$TMP"
  grep -qE '^[A-Za-z_][A-Za-z0-9_]*=' "$TMP" || die "decrypted env empty/invalid"

  sudo /usr/bin/install -m 0600 -o root -g root "$TMP" "$LIVE_ENV"
)

log_info "Deploying arr stack..."
(
  cd "$LIVE_COMPOSE_DIR"
  sudo /usr/bin/docker compose pull
  sudo /usr/bin/docker compose up -d
  sudo /usr/bin/docker compose ps
)

log_success "arr stack deployed"
