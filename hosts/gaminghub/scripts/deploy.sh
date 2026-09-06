#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# deploy.sh (GamingHub - Multi-Service) — Model B GitOps
#
# Usage:
#   deploy.sh              — Deploy ALL game servers (manual full redeploy)
#   deploy.sh minecraft    — Deploy only Minecraft
#   deploy.sh rust         — Deploy only Rust
#   deploy.sh ark-island   — Deploy only Ark Island
#   (etc.)
#
# GitOps manages:
#   - compose files: hosts/gaminghub/compose/*.yml  -> /home/*/*-hub/compose/docker-compose.yml
#   - env secrets:   hosts/gaminghub/secrets/*.env.sops -> /home/*/*-hub/compose/.env (root:root 0600)
#
# Valid targets:
#   minecraft, valheim, rust, astroneer,
#   ark-island, ark-ragnarok, ark-fjordur,
#   sotf, palworld, satisfactory, windrose
#
# Notes:
#   - One host-level Age key at /etc/sops/age/keys.txt by default
#   - docker compose is executed inside each stack dir
#   - When called with a target, ONLY that game is synced + deployed
#   - When called without args, ALL games are synced + deployed
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

# -------- Helpers --------
require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing command: $1"
}

# -------- Target selection --------
TARGET="${1:-all}"

# Validate target
VALID_TARGETS="all minecraft valheim rust astroneer ark-island ark-ragnarok ark-fjordur ark-lostisland sotf palworld satisfactory windrose"
if ! echo "$VALID_TARGETS" | grep -qw "$TARGET"; then
  die "Invalid target: '$TARGET'. Valid targets: ${VALID_TARGETS}"
fi

# Repo root: CI uses CI_PROJECT_DIR; local uses script-relative
REPO_DIR="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
log_info "REPO_DIR=${REPO_DIR}"
log_info "TARGET=${TARGET}"

# Age key location on the host (ONE key for all services)
SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-/etc/sops/age/keys.txt}"
export SOPS_AGE_KEY_FILE

# -------- Pre-flight checks --------
require_cmd sops
require_cmd docker
require_cmd sudo

# Validate sudo permissions required by this script
sudo -n /usr/bin/docker ps >/dev/null 2>&1 || die "gitlab-runner needs NOPASSWD sudo for /usr/bin/docker"
sudo -n /usr/bin/install --version >/dev/null 2>&1 || die "gitlab-runner needs NOPASSWD sudo for /usr/bin/install"

# Ensure age key exists and is readable
[[ -f "${SOPS_AGE_KEY_FILE}" ]] || die "SOPS age key not found: ${SOPS_AGE_KEY_FILE}"
[[ -r "${SOPS_AGE_KEY_FILE}" ]] || die "SOPS age key not readable: ${SOPS_AGE_KEY_FILE}"

# -------- GitOps sync: compose file -> live --------
sync_compose() {
  local repo_yml="$1"   # e.g. minecraft.yml
  local live_dir="$2"   # e.g. /home/minecraft/minecraft-hub/compose
  local src="${REPO_DIR}/hosts/gaminghub/compose/${repo_yml}"
  local dst="${live_dir}/docker-compose.yml"

  [[ -f "$src" ]] || die "missing repo compose: $src"
  [[ -d "$live_dir" ]] || die "missing live dir: $live_dir"

  log_info "Syncing compose: ${repo_yml} -> ${dst}"
  sudo /usr/bin/install -m 0644 -o root -g root "$src" "$dst"

}

log_info "Git changes (latest commit):"
git -C "$REPO_DIR" show --name-only --pretty="format:%h %s" -1 || true
log_info ""

# ============================================================
# Deploy a single game server
# ============================================================
deploy_game() {
  local GAME_NAME="$1"     # Display name (e.g., "Minecraft Servers")
  local SECRET_NAME="$2"   # Secret file stem (e.g., "minecraft", "ark-island")
  local STACK_DIR="$3"     # Full path to compose directory

  # NOTE: do NOT rely on `set -e` inside this function. deploy_game is invoked as
  # `if ! deploy_game ...` (see run_deploy), and POSIX shells disable errexit for the
  # entire dynamic extent of a command whose status is being tested -- subshells included.
  # Every failure that must fail the deploy has to be checked explicitly, or the function
  # falls through to `return 0` and the pipeline goes green on a broken deploy.

  log_info "========================================"
  log_info "Deploying: ${GAME_NAME}"
  log_info "Stack:     ${STACK_DIR}"
  log_info "========================================"

  local SOPS_ENV_FILE="${REPO_DIR}/hosts/gaminghub/secrets/${SECRET_NAME}.env.sops"
  local LIVE_ENV_FILE="${STACK_DIR}/.env"

  [[ -d "${STACK_DIR}" ]] || { log_error "Stack dir missing: ${STACK_DIR}"; return 1; }

  # Secret file missing? Not fatal — you might not be using env vars yet.
  if [[ ! -f "${SOPS_ENV_FILE}" ]]; then
    log_warn "Encrypted env file not found: ${SOPS_ENV_FILE}"
    log_warn "Skipping secrets for ${GAME_NAME} (continuing deploy)"
    # Still deploy compose; game might not need env vars.
    if ! ( cd "${STACK_DIR}" && sudo /usr/bin/docker compose pull ); then
      log_warn "⚠️  'docker compose pull' failed for ${GAME_NAME} — continuing with the local image"
    fi
    if ! ( cd "${STACK_DIR}" && sudo /usr/bin/docker compose up -d ); then
      log_error "❌ ${GAME_NAME} FAILED: 'docker compose up -d' returned non-zero"
      ( cd "${STACK_DIR}" && sudo /usr/bin/docker compose ps ) || true
      return 1
    fi
    ( cd "${STACK_DIR}" && sudo /usr/bin/docker compose ps ) || true
    log_success "✅ ${GAME_NAME} deployed (no secrets applied)"
    echo ""
    return 0
  fi

  # Decrypt to temp file with strict perms.
  # Wrapped in `if !` because the `exit 1` below exits only the SUBSHELL -- without this
  # check the parent carried on and deployed with a stale or missing .env.
 if ! (
  # subshell so trap doesn't leak across games
  umask 077
  TMP_ENV="$(mktemp)"
  trap 'rm -f "${TMP_ENV}"' EXIT

  log_info "Decrypting secrets for ${GAME_NAME}..."
  set +x  # never echo secrets
  if ! sops -d --input-type dotenv --output-type dotenv "${SOPS_ENV_FILE}" > "${TMP_ENV}"; then
    log_error "Failed to decrypt ${SOPS_ENV_FILE}"
    exit 1
  fi

  if ! grep -qE '^[A-Za-z_][A-Za-z0-9_]*=' "${TMP_ENV}"; then
    log_warn "⚠️  Decrypted env is empty/invalid for ${GAME_NAME} (expected KEY=VALUE)"
    log_warn "⚠️  Continuing anyway"
  fi

  log_info "Writing .env to ${LIVE_ENV_FILE}"
  sudo /usr/bin/install -m 0600 -o root -g root "${TMP_ENV}" "${LIVE_ENV_FILE}" || exit 1
 ); then
   log_error "❌ ${GAME_NAME} FAILED: could not decrypt or install secrets"
   return 1
 fi

  log_info "Deploying containers..."
  # Previously this was one subshell ending in `docker compose ps`, so the subshell's status
  # was `ps`'s (always 0) and a failed `up -d` was reported as a successful deploy.
  if ! ( cd "${STACK_DIR}" && sudo /usr/bin/docker compose pull ); then
    log_warn "⚠️  'docker compose pull' failed for ${GAME_NAME} — continuing with the local image"
  fi

  if ! ( cd "${STACK_DIR}" && sudo /usr/bin/docker compose up -d ); then
    log_error "❌ ${GAME_NAME} FAILED: 'docker compose up -d' returned non-zero"
    ( cd "${STACK_DIR}" && sudo /usr/bin/docker compose ps ) || true
    return 1
  fi

  ( cd "${STACK_DIR}" && sudo /usr/bin/docker compose ps ) || true

  log_success "✅ ${GAME_NAME} deployed successfully"
  echo ""
  return 0
}

# ============================================================
# Game registry: key -> label, secret name, stack dir
# ============================================================
deploy_if_target() {
  local key="$1" label="$2" secret="$3" compose_file="$4" dir="$5"

  if [[ "$TARGET" == "all" || "$TARGET" == "$key" ]]; then
    sync_compose "$compose_file" "$dir"
    run_deploy "$label" "$secret" "$dir"
  fi
}

FAILED_GAMES=()

run_deploy() {
  local label="$1" secret="$2" dir="$3"
  if ! deploy_game "$label" "$secret" "$dir"; then
    FAILED_GAMES+=("$secret")
  fi
}

# ============================================================
# Main: Deploy targeted (or all) game servers
# ============================================================
log_info "========================================"
log_info "GamingHub Multi-Service Deployment"
log_info "Target: ${TARGET}"
log_info "========================================"
log_info ""

deploy_if_target "minecraft"    "Minecraft Servers"    "minecraft"    "minecraft.yml"    "/home/minecraft/minecraft-hub/compose"
deploy_if_target "valheim"      "Valheim"              "valheim"      "valheim.yml"      "/home/valheim/valheim-hub/compose"
deploy_if_target "rust"         "Rust"                 "rust"         "rust.yml"         "/home/rust/rust-hub/compose"
deploy_if_target "astroneer"    "Astroneer"            "astroneer"    "astroneer.yml"    "/home/astroneer/astroneer-hub/compose"
deploy_if_target "ark-island"   "Ark SE - The Island"  "ark-island"   "ark-island.yml"   "/home/arkse/arkse-hub/compose"
deploy_if_target "ark-ragnarok" "Ark SE - Ragnarok"    "ark-ragnarok" "ark-ragnarok.yml" "/home/arkse/arkse-hub-rag/compose"
deploy_if_target "ark-fjordur"  "Ark SE - Fjordur"     "ark-fjordur"  "ark-fjordur.yml"  "/home/arkse/arkse-hub-fjor/compose"
deploy_if_target "ark-lostisland" "Ark SE - Lost Island" "ark-lostisland" "ark-lostisland.yml" "/home/arkse/arkse-hub-lost/compose"
deploy_if_target "sotf"         "Sons of the Forest"   "sotf"         "sotf.yml"         "/home/sotf/sotf-hub/compose"
deploy_if_target "palworld"     "Palworld"             "palworld"     "palworld.yml"     "/home/palworld/palworld-hub/compose"
deploy_if_target "satisfactory" "Satisfactory"         "satisfactory" "satisfactory.yml" "/home/satisfactory/satisfactory-hub/compose"
deploy_if_target "windrose"     "Windrose"             "windrose"     "windrose.yml"     "/home/windrose/windrose-hub/compose"

# ============================================================
# Summary
# ============================================================
log_info "========================================"
log_info "Deployment Summary (target: ${TARGET})"
log_info "========================================"

if [[ ${#FAILED_GAMES[@]} -eq 0 ]]; then
  log_success "🎉 All targeted game servers deployed successfully!"
  exit 0
else
  log_error "❌ Failed games: ${FAILED_GAMES[*]}"
  log_error "Scroll up for the first error in each failed section."
  exit 1
fi
