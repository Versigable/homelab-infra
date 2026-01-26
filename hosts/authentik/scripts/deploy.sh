#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# deploy.sh (Authentik - Multi-Service)
#
# Deploys all services on Authentik host:
#   - Authentik SSO (4 containers)
#   - Bitwarden (11 containers)
#
# Each service has:
#   - Own encrypted secrets: hosts/authentik/secrets/<service>.env.sops
#   - Own stack directory:
#       - Authentik: /home/authentik/authentik-hub/compose
#       - Bitwarden: /opt/bitwarden/bwdata/docker
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

# Age key location on the host
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
# Deploy Authentik SSO
# ============================================================
deploy_authentik() {
    local SOPS_ENV_FILE="${REPO_DIR}/hosts/authentik/secrets/authentik.env.sops"
    local LIVE_STACK_DIR="/home/authentik/authentik-hub/compose"
    local LIVE_ENV_FILE="${LIVE_STACK_DIR}/.env"
    
    log_info "========================================"
    log_info "Deploying: Authentik SSO"
    log_info "========================================"
    
    if [ ! -f "${SOPS_ENV_FILE}" ]; then
        log_error "Encrypted env file not found: ${SOPS_ENV_FILE}"
        return 1
    fi
    
    if [ ! -d "${LIVE_STACK_DIR}" ]; then
        log_error "Stack directory does not exist: ${LIVE_STACK_DIR}"
        return 1
    fi
    
    # Decrypt
    umask 077
    local TMP_ENV
    TMP_ENV="$(mktemp)"
    trap "rm -f ${TMP_ENV}" RETURN
    
    log_info "Decrypting secrets for Authentik..."
    set +x
    sops -d --input-type dotenv --output-type dotenv "${SOPS_ENV_FILE}" > "${TMP_ENV}" || {
        log_error "Failed to decrypt ${SOPS_ENV_FILE}"
        return 1
    }
    
    if ! grep -qE '^[A-Za-z_][A-Za-z0-9_]*=' "${TMP_ENV}"; then
        log_error "Decrypted env does not look like KEY=VALUE content"
        return 1
    fi
    
    # Install
    log_info "Writing .env to ${LIVE_ENV_FILE}"
    sudo /usr/bin/install -m 0600 -o root -g root "${TMP_ENV}" "${LIVE_ENV_FILE}"
    
    # Deploy
    log_info "Deploying Authentik stack..."
    cd "${LIVE_STACK_DIR}"
    sudo /usr/bin/docker compose pull
    sudo /usr/bin/docker compose up -d
    sudo /usr/bin/docker compose ps
    
    log_success "✅ Authentik deployed successfully"
    echo ""
    return 0
}

# ============================================================
# Deploy Bitwarden
# ============================================================
deploy_bitwarden() {
    local SOPS_ENV_FILE="${REPO_DIR}/hosts/authentik/secrets/bitwarden.env.sops"
    local LIVE_STACK_DIR="/opt/bitwarden/bwdata/docker"
    local LIVE_ENV_FILE="${LIVE_STACK_DIR}/.env"
    
    log_info "========================================"
    log_info "Deploying: Bitwarden"
    log_info "========================================"
    
    # Bitwarden secrets are optional (it may have its own config mechanism)
    if [ ! -f "${SOPS_ENV_FILE}" ]; then
        log_warn "⚠️  Bitwarden encrypted env not found: ${SOPS_ENV_FILE}"
        log_warn "⚠️  If Bitwarden uses its own config, this is OK"
        log_warn "⚠️  Skipping Bitwarden secret deployment"
        
        # Still try to restart Bitwarden if the stack exists
        if [ -d "${LIVE_STACK_DIR}" ]; then
            log_info "Restarting Bitwarden stack (no secret update)..."
            cd "${LIVE_STACK_DIR}"
            sudo /usr/bin/docker compose pull
            sudo /usr/bin/docker compose up -d
            sudo /usr/bin/docker compose ps
            log_success "✅ Bitwarden restarted"
        fi
        
        return 0
    fi
    
    if [ ! -d "${LIVE_STACK_DIR}" ]; then
        log_error "Bitwarden stack directory does not exist: ${LIVE_STACK_DIR}"
        log_error "Bitwarden may use /opt/bitwarden/bwdata/ structure"
        return 1
    fi
    
    # Decrypt
    umask 077
    local TMP_ENV
    TMP_ENV="$(mktemp)"
    trap "rm -f ${TMP_ENV}" RETURN
    
    log_info "Decrypting secrets for Bitwarden..."
    set +x
    sops -d --input-type dotenv --output-type dotenv "${SOPS_ENV_FILE}" > "${TMP_ENV}" || {
        log_error "Failed to decrypt ${SOPS_ENV_FILE}"
        return 1
    }
    
    # Install
    log_info "Writing .env to ${LIVE_ENV_FILE}"
    sudo /usr/bin/install -m 0600 -o root -g root "${TMP_ENV}" "${LIVE_ENV_FILE}"
    
    # Deploy
    log_info "Deploying Bitwarden stack..."
    cd "${LIVE_STACK_DIR}"
    sudo /usr/bin/docker compose pull
    sudo /usr/bin/docker compose up -d
    sudo /usr/bin/docker compose ps
    
    log_success "✅ Bitwarden deployed successfully"
    echo ""
    return 0
}

# ============================================================
# Main: Deploy all services on this host
# ============================================================
FAILED_SERVICES=()

log_info "========================================"
log_info "Authentik Host Multi-Service Deployment"
log_info "========================================"
log_info "Services: Authentik SSO, Bitwarden"
log_info ""

if ! deploy_authentik; then
    log_error "❌ Failed to deploy Authentik"
    FAILED_SERVICES+=("authentik")
fi

if ! deploy_bitwarden; then
    log_error "❌ Failed to deploy Bitwarden"
    FAILED_SERVICES+=("bitwarden")
fi

# Summary
log_info "========================================"
log_info "Deployment Summary"
log_info "========================================"

if [ ${#FAILED_SERVICES[@]} -eq 0 ]; then
    log_success "🎉 All services deployed successfully!"
    exit 0
else
    log_error "❌ Failed services: ${FAILED_SERVICES[*]}"
    log_error "Check logs above for details"
    exit 1
fi
