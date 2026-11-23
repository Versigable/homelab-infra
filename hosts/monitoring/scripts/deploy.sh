#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# deploy.sh (Monitoring) — Model B GitOps
#
# Deploys the homelab monitoring stack:
#   Prometheus + Grafana + Alertmanager + heartbeat sidecar + ntfy
# to /home/monitoring/monitoring-hub/.
#
# Secrets: monitoring.env.sops + ntfy.env.sops are decrypted and concatenated
# into the live compose .env (root 0600). lab-activity.env.sops is kept out of
# .env: its Jellyfin key becomes a 0400 file for the lab-activity container.
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
#   - (data/ntfy is created by ensure_data_dirs on every deploy, 65534:65534)
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
REPO_NTFY_SOPS_ENV="${REPO_DIR}/hosts/monitoring/secrets/ntfy.env.sops"
REPO_LA_SOPS_ENV="${REPO_DIR}/hosts/monitoring/secrets/lab-activity.env.sops"
REPO_AMGR_TMPL="${REPO_CONFIG_DIR}/alertmanager/alertmanager.yml.tmpl"

log_info "REPO_DIR=${REPO_DIR}"
log_info "TARGET=${TARGET}"

# -------- Pre-flight --------
require_cmd sops
require_cmd docker
require_cmd sudo
sudo -n /usr/bin/docker ps >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/docker"
sudo -n /usr/bin/install --version >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/install"
[[ -f "${SOPS_AGE_KEY_FILE}" && -r "${SOPS_AGE_KEY_FILE}" ]] || die "sops age key not readable: ${SOPS_AGE_KEY_FILE}"
[[ -f "${REPO_COMPOSE}" ]]   || die "missing compose: ${REPO_COMPOSE}"
[[ -f "${REPO_SOPS_ENV}" ]]  || die "missing encrypted env: ${REPO_SOPS_ENV}"
[[ -f "${REPO_NTFY_SOPS_ENV}" ]] || die "missing encrypted env: ${REPO_NTFY_SOPS_ENV}"
[[ -f "${REPO_LA_SOPS_ENV}" ]] || die "missing encrypted env: ${REPO_LA_SOPS_ENV}"
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
GRAFANA_RESTART=0
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
  # lab-activity exporter: script + server list
  sudo /usr/bin/install -d -m 0755 -o root -g root "${LIVE_CONFIG_DIR}/lab-activity"
  shopt -s nullglob
  for f in "${REPO_CONFIG_DIR}/lab-activity/"*.{py,json}; do
    sudo /usr/bin/install -m 0644 -o root -g root "$f" \
      "${LIVE_CONFIG_DIR}/lab-activity/$(basename "$f")"
  done
  shopt -u nullglob

  if [[ -d "${REPO_CONFIG_DIR}/grafana/provisioning" ]]; then
    log_info "Syncing grafana provisioning"
    sudo /usr/bin/install -d -m 0755 -o root -g root "${LIVE_CONFIG_DIR}/grafana"
    sudo /usr/bin/install -d -m 0755 -o root -g root "${LIVE_CONFIG_DIR}/grafana/provisioning"
    for subdir in datasources dashboards notifiers plugins; do
      if [[ -d "${REPO_CONFIG_DIR}/grafana/provisioning/${subdir}" ]]; then
        sudo /usr/bin/install -d -m 0755 -o root -g root "${LIVE_CONFIG_DIR}/grafana/provisioning/${subdir}"
        shopt -s nullglob
        for f in "${REPO_CONFIG_DIR}/grafana/provisioning/${subdir}"/*.{yaml,yml,json}; do
          # Grafana reads provider/datasource YAML only at startup (dashboard JSON is
          # rescanned every minute), so a changed YAML means a Grafana restart.
          if [[ "$f" == *.y*ml ]] && ! cmp -s "$f" "${LIVE_CONFIG_DIR}/grafana/provisioning/${subdir}/$(basename "$f")"; then
            GRAFANA_RESTART=1
          fi
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
  log_info "Decrypting secrets + installing alertmanager config and secret files"
  (
    umask 077
    local tmp_env tmp_amgr
    tmp_env="$(mktemp)"
    tmp_amgr="$(mktemp)"
    trap 'rm -f "${tmp_env}" "${tmp_amgr}"' EXIT

    set +x
    sops -d --input-type dotenv --output-type dotenv "${REPO_SOPS_ENV}" > "${tmp_env}"
    grep -qE '^[A-Za-z_][A-Za-z0-9_]*=' "${tmp_env}" || die "decrypted env empty/invalid"
    # ntfy secrets live in their own file. Its NTFY_AUTH_* values are
    # single-quoted, so bcrypt's `$` survives both `. tmp_env` here and
    # compose's .env parsing.
    printf '\n' >> "${tmp_env}"
    sops -d --input-type dotenv --output-type dotenv "${REPO_NTFY_SOPS_ENV}" >> "${tmp_env}"
    grep -qE '^NTFY_AUTH_USERS=' "${tmp_env}" || die "decrypted ntfy env empty/invalid"

    set -a
    # shellcheck source=/dev/null
    . "${tmp_env}"
    set +a

    # alertmanager.yml holds no secrets: Alertmanager reads the Discord URL
    # and the ntfy token from 0400 files owned by its user (65534), mounted
    # read-only at /run/secrets/alertmanager. Reading the config is safe.
    [[ -n "${DISCORD_WEBHOOK_URL:-}" ]] || die "DISCORD_WEBHOOK_URL missing"
    [[ -n "${NTFY_ALERTMANAGER_TOKEN:-}" ]] || die "NTFY_ALERTMANAGER_TOKEN missing"
    local tmp_dc tmp_nt
    tmp_dc="$(mktemp)"; tmp_nt="$(mktemp)"
    trap 'rm -f "${tmp_env}" "${tmp_amgr}" "${tmp_dc}" "${tmp_nt}"' EXIT
    printf '%s/slack' "${DISCORD_WEBHOOK_URL}" > "${tmp_dc}"
    printf '%s' "${NTFY_ALERTMANAGER_TOKEN}" > "${tmp_nt}"
    sudo /usr/bin/install -d -m 0700 -o root -g root "${LIVE_HUB}/secrets"
    sudo /usr/bin/install -d -m 0500 -o 65534 -g 65534 "${LIVE_HUB}/secrets/alertmanager"
    sudo /usr/bin/install -m 0400 -o 65534 -g 65534 "${tmp_dc}" "${LIVE_HUB}/secrets/alertmanager/discord_slack_url"
    sudo /usr/bin/install -m 0400 -o 65534 -g 65534 "${tmp_nt}" "${LIVE_HUB}/secrets/alertmanager/ntfy_token"

    # lab-activity: Jellyfin API key (read /Sessions for counts), not in .env
    local tmp_jf
    tmp_jf="$(mktemp)"
    trap 'rm -f "${tmp_env}" "${tmp_amgr}" "${tmp_dc}" "${tmp_nt}" "${tmp_jf}"' EXIT
    sops -d --input-type dotenv --output-type dotenv "${REPO_LA_SOPS_ENV}" \
      | sed -n 's/^JELLYFIN_KEY=//p' | tr -d '\n' > "${tmp_jf}"
    [[ -s "${tmp_jf}" ]] || die "JELLYFIN_KEY missing in lab-activity.env.sops"
    sudo /usr/bin/install -d -m 0500 -o 65534 -g 65534 "${LIVE_HUB}/secrets/lab-activity"
    sudo /usr/bin/install -m 0400 -o 65534 -g 65534 "${tmp_jf}" "${LIVE_HUB}/secrets/lab-activity/jellyfin_key"

    sudo /usr/bin/install -m 0600 -o root -g root "${tmp_env}"  "${LIVE_COMPOSE_DIR}/.env"
    sudo /usr/bin/install -m 0644 -o root -g root "${REPO_AMGR_TMPL}" "${LIVE_CONFIG_DIR}/alertmanager/alertmanager.yml"
  )
}

# -------- Data dirs for services added after the one-time bootstrap --------
ensure_data_dirs() {
  log_info "Ensuring data dirs"
  sudo /usr/bin/install -d -m 0750 -o 65534 -g 65534 "${LIVE_HUB}/data/ntfy"
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

# -------- SIGHUP reload --------
# Compose recreates containers when their compose section changes (image bump,
# env var change, volume change, etc.) → fresh config loaded naturally.
# Compose does NOT recreate on config-file-only changes (alert rules edit,
# scrape target edit, alertmanager template edit). For those, prometheus +
# alertmanager need an explicit reload. SIGHUP works for both.
#
# Using `docker kill --signal=HUP` rather than POST /-/reload because the
# Prometheus image is FROM scratch — no shell, no curl, no wget. Docker
# delivers the signal via the kernel directly, no in-container binary needed.
reload_via_sighup() {
  local container="$1"
  for attempt in 1 2 3; do
    local state
    state=$(sudo /usr/bin/docker inspect "$container" --format '{{.State.Status}}' 2>/dev/null || echo "missing")
    if [[ "$state" == "running" ]]; then
      if sudo /usr/bin/docker kill --signal=HUP "$container" >/dev/null 2>&1; then
        log_success "  ✓ $container reloaded (SIGHUP)"
        return 0
      fi
    fi
    sleep 2
  done
  log_warn "  $container not running after 6s — reload skipped"
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
ensure_data_dirs
compose_up

# Reload prometheus + alertmanager so config-file-only changes (alert rule
# edits, scrape target edits, alertmanager template edits) take effect
# without needing a container restart. No-op when compose just recreated
# the container — but cheap, idempotent, and prevents the "I pushed but
# Prometheus is still using old rules" surprise.
log_info "Post-deploy SIGHUP reload"
reload_via_sighup prometheus
reload_via_sighup alertmanager
if [[ "${GRAFANA_RESTART:-0}" == 1 ]]; then
  sudo /usr/bin/docker restart grafana >/dev/null && log_success "  ✓ grafana restarted (provisioning YAML changed)"
fi
# lab-activity has no reload; it's stateless, so a restart picks up script edits.
sudo /usr/bin/docker restart lab-activity >/dev/null && log_success "  ✓ lab-activity restarted"

log_success "🎉 Monitoring stack deployed successfully"
