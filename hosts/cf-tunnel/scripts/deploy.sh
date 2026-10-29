#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# deploy.sh (cf-tunnel) — Model B GitOps
#
# Deploys the cloudflared replicas to /home/cf-tunnel/cf-tunnel-hub on LXC 117
# (cf-tunnel-1, phoenix) and LXC 118 (cf-tunnel-2, rogue). The same files go to both.
# There is no manual bootstrap: the LXCs, their runners, the runner's sudoers, the SOPS
# age keys and the directory layout all come from iac/ (Terraform + Ansible).
#
# A service starts only when its secret exists (secrets/org.env.sops -> cf-org,
# secrets/us.env.sops -> cf-us), and is removed when its secret goes away, so each
# tunnel can move here on its own schedule.
#
# Usage: deploy.sh
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

# -------- Paths --------
REPO_DIR="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-/etc/sops/age/keys.txt}"
export SOPS_AGE_KEY_FILE

LIVE_HUB="/home/cf-tunnel/cf-tunnel-hub"
LIVE_COMPOSE_DIR="${LIVE_HUB}/compose"
LIVE_CONFIG_DIR="${LIVE_HUB}/config"

REPO_APP="${REPO_DIR}/hosts/cf-tunnel"
REPO_COMPOSE="${REPO_APP}/compose/cf-tunnel.yml"
REPO_ORG_CONFIG="${REPO_APP}/config/cloudflared-org.yml"

# secret name -> compose service, and the metrics port deploy.sh checks
declare -A SERVICE=( [org]=cf-org [us]=cf-us )
declare -A METRICS_PORT=( [org]=20241 [us]=20242 )

# -------- Pre-flight --------
require_cmd sops
require_cmd docker
require_cmd sudo
require_cmd curl
sudo -n /usr/bin/docker ps >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/docker"
sudo -n /usr/bin/install --version >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/install"
[[ -r "${SOPS_AGE_KEY_FILE}" ]] || die "sops age key not readable: ${SOPS_AGE_KEY_FILE}"
for f in "${REPO_COMPOSE}" "${REPO_ORG_CONFIG}"; do
  [[ -f "$f" ]] || die "missing: $f"
done
[[ -d "${LIVE_HUB}" ]] || die "missing live hub: ${LIVE_HUB} (run iac/ansible site.yml)"

git -C "$REPO_DIR" show --name-only --pretty="format:%h %s" -1 || true
echo

compose() { (cd "${LIVE_COMPOSE_DIR}" && sudo /usr/bin/docker compose "$@"); }

# -------- Secrets --------
# Fills ENABLED with the secrets that exist and writes each one's env file. Removes the
# env file of a secret that no longer exists.
ENABLED=()
render_envs() {
  local name sops_file live_env
  for name in org us; do
    sops_file="${REPO_APP}/secrets/${name}.env.sops"
    live_env="${LIVE_COMPOSE_DIR}/${name}.env"
    if [[ ! -f "${sops_file}" ]]; then
      log_warn "No secrets/${name}.env.sops: ${SERVICE[$name]} stays off"
      sudo /usr/bin/install -m 0600 -o root -g root /dev/null "${live_env}"
      continue
    fi
    log_info "Decrypting secrets/${name}.env.sops -> ${name}.env"
    (
      umask 077
      tmp=$(mktemp)
      trap 'rm -f "${tmp}"' EXIT
      sops -d --input-type dotenv --output-type dotenv "${sops_file}" > "${tmp}"
      grep -qE '^TUNNEL_TOKEN=.' "${tmp}" || die "secrets/${name}.env.sops is missing TUNNEL_TOKEN"
      sudo /usr/bin/install -m 0600 -o root -g root "${tmp}" "${live_env}"
    )
    ENABLED+=("${name}")
  done
}

sync_files() {
  log_info "Syncing the compose file and the cf-org config"
  sudo /usr/bin/install -m 0644 -o root -g root "${REPO_COMPOSE}" "${LIVE_COMPOSE_DIR}/docker-compose.yml"
  sudo /usr/bin/install -m 0644 -o root -g root "${REPO_ORG_CONFIG}" "${LIVE_CONFIG_DIR}/cloudflared-org.yml"
  # cloudflared reads its config only at startup, and `compose up -d` does not notice a
  # changed bind-mounted file. The config's hash goes into a label on cf-org (through
  # .env), so a config edit changes the service and compose recreates cf-org.
  local tmp
  tmp=$(mktemp)
  echo "CF_ORG_CONFIG_SHA=$(sha256sum "${REPO_ORG_CONFIG}" | cut -d' ' -f1)" > "${tmp}"
  sudo /usr/bin/install -m 0644 -o root -g root "${tmp}" "${LIVE_COMPOSE_DIR}/.env"
  rm -f "${tmp}"
}

compose_up() {
  local name services=() off=()
  for name in "${ENABLED[@]}"; do services+=("${SERVICE[$name]}"); done
  for name in org us; do
    [[ " ${ENABLED[*]} " == *" ${name} "* ]] || off+=("${SERVICE[$name]}")
  done
  if (( ${#off[@]} )); then
    log_info "Removing services without a secret: ${off[*]}"
    compose rm --stop --force "${off[@]}"
  fi
  if (( ${#services[@]} == 0 )); then
    log_warn "No tunnel secrets yet, nothing to start"
    return 0
  fi
  log_info "Pulling and starting: ${services[*]}"
  compose pull --quiet "${services[@]}"
  compose up -d --remove-orphans "${services[@]}"
  compose ps
}

# -------- Checks --------
# /ready answers 200 once the replica has registered at least one connection to the edge.
wait_ready() {
  local name port i body
  for name in "${ENABLED[@]}"; do
    port="${METRICS_PORT[$name]}"
    log_info "Waiting for ${SERVICE[$name]} to register with Cloudflare (up to 2 min)"
    for i in $(seq 1 24); do
      body=$(curl -sf -m 5 "http://127.0.0.1:${port}/ready" || true)
      if [[ -n "${body}" ]]; then
        log_success "${SERVICE[$name]} ready: ${body}"
        continue 2
      fi
      sleep 5
    done
    sudo /usr/bin/docker logs --tail 40 "${SERVICE[$name]}" 2>&1 || true
    die "${SERVICE[$name]} did not register within 2 minutes"
  done
}

log_info "========================================"
log_info "cf-tunnel deployment on $(hostname)"
log_info "========================================"
render_envs
sync_files
compose_up
wait_ready
log_success "cf-tunnel deployed: ${ENABLED[*]:-nothing}"
