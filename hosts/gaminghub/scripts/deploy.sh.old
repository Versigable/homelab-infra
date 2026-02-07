#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# deploy.sh (GamingHub - Multi-Service)
#
# Deploys all game servers on GamingHub host:
#   - Minecraft (2 servers) → /home/minecraft/minecraft-hub/compose
#   - Valheim → /home/valheim/valheim-hub/compose
#   - Astroneer  ^f^r /home/astroneer/astroneer-hub/compose
#   - Ark SE (3 maps):
#       - Island → /home/arkse/arkse-hub/compose
#       - Ragnarok → /home/arkse/arkse-hub-rag/compose
#       - Fjordur → /home/arkse/arkse-hub-fjor/compose
#   - Sons of the Forest → /home/sotf/sotf-hub/compose
#   - Palworld → /home/palworld/palworld-hub/compose
#   - Satisfactory → /home/satisfactory/satisfactory-hub/compose
#
# All services use the same SOPS key (host-level encryption)
# Each service has its own secret file: hosts/gaminghub/secrets/<game>.env.sops
# ============================================================

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[deploy]${NC} $*"; }
log_success() { echo -e "${GREEN}[deploy]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[deploy]${NC} $*"; }
log_error() { echo -e "${RED}[deploy]${NC} $*"; }

# Repo root (where CI checks out the repo)
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
log_info "REPO_DIR=${REPO_DIR}"

sync_compose_map() {
  local repo_yml="$1"  # e.g. minecraft.yml
  local live_dir="$2"  # e.g. /home/minecraft/minecraft-hub/compose
  local src="${REPO_DIR}/hosts/gaminghub/compose/${repo_yml}"
  local dst="${live_dir}/docker-compose.yml"

  [[ -f "$src" ]] || die "missing repo compose: $src"
  [[ -d "$live_dir" ]] || die "missing live dir: $live_dir"

  log "Syncing ${repo_yml} -> ${dst}"
  sudo /usr/bin/install -m 0644 -o root -g root "$src" "$dst"
}

# Map repo file -> live directory
sync_compose_map "minecraft.yml"    "/home/minecraft/minecraft-hub/compose"
sync_compose_map "valheim.yml"      "/home/valheim/valheim-hub/compose"
sync_compose_map "sotf.yml"         "/home/sotf/sotf-hub/compose"
sync_compose_map "palworld.yml"     "/home/palworld/palworld-hub/compose"
sync_compose_map "satisfactory.yml" "/home/satisfactory/satisfactory-hub/compose"
sync_compose_map "astroneer.yml"    "/home/astroneer/astroneer-hub/compose"
sync_compose_map "ark-island.yml"   "/home/arkse/arkse-hub/compose"
sync_compose_map "ark-ragnarok.yml" "/home/arkse/arkse-hub-rag/compose"
sync_compose_map "ark-fjordur.yml"  "/home/arkse/arkse-hub-fjor/compose"

# Age key location on the host (ONE key for all services)
SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-/etc/sops/age/keys.txt}"
export SOPS_AGE_KEY_FILE

# Pre-flight checks
require_cmd() { 
    command -v "$1" >/dev/null 2>&1 || { 
        log_error "Missing command: $1" 
        exit 1
    }
}

require_cmd sops
require_cmd docker
require_cmd sudo

# Validate sudo permissions
sudo -n /usr/bin/docker ps >/dev/null || {
    log_error "gitlab-runner needs NOPASSWD sudo for docker"
    exit 1
}
sudo -n /usr/bin/install --version >/dev/null || {
    log_error "gitlab-runner needs NOPASSWD sudo for install"
    exit 1
}

# Ensure age key exists and is readable
if [ ! -f "${SOPS_AGE_KEY_FILE}" ]; then
    log_error "SOPS age key not found at ${SOPS_AGE_KEY_FILE}"
    exit 1
fi
if [ ! -r "${SOPS_AGE_KEY_FILE}" ]; then
    log_error "SOPS age key exists but is not readable: ${SOPS_AGE_KEY_FILE}"
    exit 1
fi

# ============================================================
# Deploy a single game server
# ============================================================
deploy_game() {
    local GAME_NAME="$1"      # Display name (e.g., "Minecraft", "Ark Island")
    local SECRET_NAME="$2"    # Secret file name (e.g., "minecraft", "ark-island")
    local STACK_DIR="$3"      # Full path to compose directory
    
    log_info "========================================"
    log_info "Deploying: ${GAME_NAME}"
    log_info "========================================"
    
    local SOPS_ENV_FILE="${REPO_DIR}/hosts/gaminghub/secrets/${SECRET_NAME}.env.sops"
    local LIVE_ENV_FILE="${STACK_DIR}/.env"
    
    # Check if encrypted secret exists
    if [ ! -f "${SOPS_ENV_FILE}" ]; then
        log_warn "⚠️  Encrypted env file not found: ${SOPS_ENV_FILE}"
        log_warn "⚠️  Skipping ${GAME_NAME} (may not have secrets configured yet)"
        return 0  # Not a failure - some games may not need secrets
    fi
    
    # Check if stack directory exists
    if [ ! -d "${STACK_DIR}" ]; then
        log_error "Stack directory does not exist: ${STACK_DIR}"
        return 1
    fi
    
    # Decrypt to temp file with strict perms
    umask 077
    local TMP_ENV
    TMP_ENV="$(mktemp)"
    trap "rm -f ${TMP_ENV}" RETURN
    
    log_info "Decrypting secrets for ${GAME_NAME}..."
    set +x  # Never echo secrets
    if ! sops -d --input-type dotenv --output-type dotenv "${SOPS_ENV_FILE}" > "${TMP_ENV}"; then
        log_error "Failed to decrypt ${SOPS_ENV_FILE}"
        return 1
    fi
    
    # Sanity check: ensure it looks like KEY=VALUE content
    if ! grep -qE '^[A-Za-z_][A-Za-z0-9_]*=' "${TMP_ENV}"; then
        log_warn "⚠️  Decrypted env is empty or invalid for ${GAME_NAME}"
        log_warn "⚠️  Continuing anyway (game may not need env vars)"
    fi
    
    # Install decrypted .env to live location as root:root 0600
    log_info "Writing .env to ${LIVE_ENV_FILE}"
    sudo /usr/bin/install -m 0600 -o root -g root "${TMP_ENV}" "${LIVE_ENV_FILE}"
    
    # Deploy the game server
    log_info "Deploying ${GAME_NAME} server..."
    cd "${STACK_DIR}"
    
    sudo /usr/bin/docker compose pull
    sudo /usr/bin/docker compose up -d
    sudo /usr/bin/docker compose ps
    
    log_success "✅ ${GAME_NAME} deployed successfully"
    echo ""
    
    return 0
}

# ============================================================
# Main: Deploy all game servers on this host
# ============================================================

log_info "========================================"
log_info "GamingHub Multi-Service Deployment"
log_info "========================================"
log_info ""

FAILED_GAMES=()

# Minecraft (2 servers in one hub)
if ! deploy_game "Minecraft Servers" "minecraft" "/home/minecraft/minecraft-hub/compose"; then
    FAILED_GAMES+=("minecraft")
fi

# Valheim
if ! deploy_game "Valheim" "valheim" "/home/valheim/valheim-hub/compose"; then
    FAILED_GAMES+=("valheim")
fi

# Astroneer
if ! deploy_game "Astroneer" "astroneer" "/home/astroneer/astroneer-hub/compose"; then
    FAILED_GAMES+=("astroneer")
fi

# Ark SE - Island
if ! deploy_game "Ark SE - The Island" "ark-island" "/home/arkse/arkse-hub/compose"; then
    FAILED_GAMES+=("ark-island")
fi

# Ark SE - Ragnarok
if ! deploy_game "Ark SE - Ragnarok" "ark-ragnarok" "/home/arkse/arkse-hub-rag/compose"; then
    FAILED_GAMES+=("ark-ragnarok")
fi

# Ark SE - Fjordur
if ! deploy_game "Ark SE - Fjordur" "ark-fjordur" "/home/arkse/arkse-hub-fjor/compose"; then
    FAILED_GAMES+=("ark-fjordur")
fi

# Sons of the Forest
if ! deploy_game "Sons of the Forest" "sotf" "/home/sotf/sotf-hub/compose"; then
    FAILED_GAMES+=("sotf")
fi

# Palworld
if ! deploy_game "Palworld" "palworld" "/home/palworld/palworld-hub/compose"; then
    FAILED_GAMES+=("palworld")
fi

# Satisfactory
if ! deploy_game "Satisfactory" "satisfactory" "/home/satisfactory/satisfactory-hub/compose"; then
    FAILED_GAMES+=("satisfactory")
fi

# Summary
log_info "========================================"
log_info "Deployment Summary"
log_info "========================================"

if [ ${#FAILED_GAMES[@]} -eq 0 ]; then
    log_success "🎉 All game servers deployed successfully!"
    exit 0
else
    log_error "❌ Failed games: ${FAILED_GAMES[*]}"
    log_error "Check logs above for details"
    exit 1
fi
