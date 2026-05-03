#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# deploy.sh (Monitoring) — Model B GitOps
#
# Deploys the homelab monitoring stack:
#   Prometheus + Grafana + Alertmanager + heartbeat sidecar
# to /home/monitoring/monitoring-hub/.
#
# Usage:
#   deploy.sh             — Deploy the monitoring stack
#   deploy.sh monitoring  — Same (explicit target)
#
# One-time bootstrap (run as root on the LXC, before first deploy):
#   - useradd -m monitoring
#   - mkdir -p /home/monitoring/monitoring-hub/{compose,config/{prometheus/alerts,alertmanager},data/{prometheus,grafana,alertmanager}}
#   - chown -R 65534:65534 /home/monitoring/monitoring-hub/data/{prometheus,alertmanager}
#   - chown 472:472 /home/monitoring/monitoring-hub/data/grafana
#   - apt install gettext-base acl
#   - setfacl -m u:gitlab-runner:rx /home/monitoring{,/monitoring-hub}
#   - setfacl -R -m u:gitlab-runner:rwx /home/monitoring/monitoring-hub/{compose,config}
#   - sudoers entry: gitlab-runner ALL=(ALL) NOPASSWD: /usr/bin/docker, /usr/bin/install, /usr/bin/setfacl
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
require_cmd() { command -v "$1" >/dev/null 2>&1 || die "missing command: $1"; }

# -------- Target --------
TARGET="${1:-monitoring}"
[[ "$TARGET" == "monitoring" ]] || die "Invalid target: '$TARGET'. Only 'monitoring' is valid."

# -------- Paths --------
REPO_DIR="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-/etc/sops/age/keys.txt}"
export SOPS_AGE_KEY_FILE

LIVE_HUB="/home/monitoring/monitoring-hub"
LIVE_COMPOSE_DIR="${LIVE_HUB}/compose"
LIVE_CONFIG_DIR="${LIVE_HUB}/config"

REPO_COMPOSE="${REPO_DIR}/hosts/monitoring/compose/monitoring.yml"
REPO_CONFIG_DIR="${REPO_DIR}/hosts/monitoring/config"
REPO_SOPS_ENV="${REPO_DIR}/hosts/monitoring/secrets/monitoring.env.sops"
REPO_AMGR_TMPL="${REPO_CONFIG_DIR}/alertmanager/alertmanager.yml.tmpl"

log_info "REPO_DIR=${REPO_DIR}"
log_info "TARGET=${TARGET}"

# -------- Pre-flight --------
require_cmd sops
require_cmd docker
require_cmd sudo
require_cmd envsubst
sudo -n /usr/bin/docker ps >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/docker"
sudo -n /usr/bin/install --version >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/install"
[[ -f "${SOPS_AGE_KEY_FILE}" && -r "${SOPS_AGE_KEY_FILE}" ]] || die "sops age key not readable: ${SOPS_AGE_KEY_FILE}"
[[ -f "${REPO_COMPOSE}" ]]   || die "missing compose: ${REPO_COMPOSE}"
[[ -f "${REPO_SOPS_ENV}" ]]  || die "missing encrypted env: ${REPO_SOPS_ENV}"
[[ -f "${REPO_AMGR_TMPL}" ]] || die "missing alertmanager template: ${REPO_AMGR_TMPL}"
[[ -d "${LIVE_HUB}" ]]       || die "missing live hub: ${LIVE_HUB} (run one-time bootstrap on the LXC)"

log_info "Git changes (latest commit):"
git -C "$REPO_DIR" show --name-only --pretty="format:%h %s" -1 || true
log_info ""

# -------- Self-heal runner ACLs --------
ensure_runner_acls() {
  log_info "Self-heal runner ACLs"
  sudo /usr/bin/setfacl -m "u:gitlab-runner:rx" "/home/monitoring"             2>/dev/null || true
  sudo /usr/bin/setfacl -m "u:gitlab-runner:rx" "${LIVE_HUB}"                  2>/dev/null || true
  sudo /usr/bin/setfacl -R -m "u:gitlab-runner:rwx" "${LIVE_COMPOSE_DIR}"      2>/dev/null || true
  sudo /usr/bin/setfacl -d -m "u:gitlab-runner:rwx" "${LIVE_COMPOSE_DIR}"      2>/dev/null || true
  sudo /usr/bin/setfacl -R -m "u:gitlab-runner:rwx" "${LIVE_CONFIG_DIR}"       2>/dev/null || true
  sudo /usr/bin/setfacl -d -m "u:gitlab-runner:rwx" "${LIVE_CONFIG_DIR}"       2>/dev/null || true
}

# -------- Sync compose file --------
sync_compose() {
  log_info "Syncing compose -> ${LIVE_COMPOSE_DIR}/docker-compose.yml"
  sudo /usr/bin/install -m 0644 -o root -g root "${REPO_COMPOSE}" "${LIVE_COMPOSE_DIR}/docker-compose.yml"
}

# -------- Sync static configs (prometheus.yml + alert rules) --------
sync_static_configs() {
  log_info "Syncing static configs"
  sudo /usr/bin/install -d -m 0755 -o root -g root "${LIVE_CONFIG_DIR}/prometheus"
  sudo /usr/bin/install -d -m 0755 -o root -g root "${LIVE_CONFIG_DIR}/prometheus/alerts"
  sudo /usr/bin/install -d -m 0755 -o root -g root "${LIVE_CONFIG_DIR}/alertmanager"

  sudo /usr/bin/install -m 0644 -o root -g root \
    "${REPO_CONFIG_DIR}/prometheus/prometheus.yml" \
    "${LIVE_CONFIG_DIR}/prometheus/prometheus.yml"

  shopt -s nullglob
  for f in "${REPO_CONFIG_DIR}/prometheus/alerts/"*.yml; do
    sudo /usr/bin/install -m 0644 -o root -g root "$f" \
      "${LIVE_CONFIG_DIR}/prometheus/alerts/$(basename "$f")"
  done
  shopt -u nullglob

  # ---- Grafana provisioning (datasources, dashboards) ----
  if [[ -d "${REPO_CONFIG_DIR}/grafana/provisioning" ]]; then
    log_info "Syncing grafana provisioning"
    sudo /usr/bin/install -d -m 0755 -o root -g root "${LIVE_CONFIG_DIR}/grafana"
    sudo /usr/bin/install -d -m 0755 -o root -g root "${LIVE_CONFIG_DIR}/grafana/provisioning"
    for subdir in datasources dashboards notifiers plugins; do
      if [[ -d "${REPO_CONFIG_DIR}/grafana/provisioning/${subdir}" ]]; then
        sudo /usr/bin/install -d -m 0755 -o root -g root "${LIVE_CONFIG_DIR}/grafana/provisioning/${subdir}"
        shopt -s nullglob
        for f in "${REPO_CONFIG_DIR}/grafana/provisioning/${subdir}"/*.{yaml,yml,json}; do
          sudo /usr/bin/install -m 0644 -o root -g root "$f" \
            "${LIVE_CONFIG_DIR}/grafana/provisioning/${subdir}/$(basename "$f")"
        done
        shopt -u nullglob
      fi
    done
  fi
}

# -------- Decrypt secrets + render alertmanager.yml + install both --------
decrypt_and_render() {
  log_info "Decrypting secrets + rendering alertmanager template"
  (
    umask 077
    local tmp_env tmp_amgr
    tmp_env="$(mktemp)"
    tmp_amgr="$(mktemp)"
    trap 'rm -f "${tmp_env}" "${tmp_amgr}"' EXIT

    set +x
    sops -d --input-type dotenv --output-type dotenv "${REPO_SOPS_ENV}" > "${tmp_env}"
    grep -qE '^[A-Za-z_][A-Za-z0-9_]*=' "${tmp_env}" || die "decrypted env empty/invalid"

    set -a
    # shellcheck source=/dev/null
    . "${tmp_env}"
    set +a

    # Whitelist substitution: only ${DISCORD_WEBHOOK_URL} is replaced;
    # any other ${...} in the template (Alertmanager has none today, but
    # this protects future edits from accidental substitution) passes through.
    envsubst '${DISCORD_WEBHOOK_URL}' < "${REPO_AMGR_TMPL}" > "${tmp_amgr}"

    sudo /usr/bin/install -m 0600 -o root -g root "${tmp_env}"  "${LIVE_COMPOSE_DIR}/.env"
    sudo /usr/bin/install -m 0644 -o root -g root "${tmp_amgr}" "${LIVE_CONFIG_DIR}/alertmanager/alertmanager.yml"
  )
}

# -------- Compose up --------
compose_up() {
  log_info "docker compose pull && up -d"
  (
    cd "${LIVE_COMPOSE_DIR}"
    sudo /usr/bin/docker compose pull
    sudo /usr/bin/docker compose up -d
    sudo /usr/bin/docker compose ps
  )
}

# ============================================================
# Main
# ============================================================
log_info "========================================"
log_info "Monitoring Stack Deployment (target: ${TARGET})"
log_info "========================================"
log_info ""

ensure_runner_acls
sync_compose
sync_static_configs
decrypt_and_render
compose_up

log_success "🎉 Monitoring stack deployed successfully"
