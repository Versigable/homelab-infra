#!/usr/bin/env bash
set -euo pipefail
# Several steps run inside $(...), and a failure there must stop the deploy too.
shopt -s inherit_errexit

# ============================================================
# deploy.sh (Home Assistant) — Model B GitOps
#
# Deploys Home Assistant + Mosquitto (+ Zigbee2MQTT) to /home/home-assistant/home-assistant-hub
# on LXC 112. There is no manual bootstrap: the LXC, its runner, the runner's sudoers, the
# SOPS age key and the directory layout all come from iac/ (Terraform + Ansible).
#
# configuration.yaml comes from git and is checked with Home Assistant's check_config before
# it goes live. Everything set up in the UI stays in /config/.storage and is never touched.
# Zigbee2MQTT stays off until config/zigbee.env names the radio and the channel.
#
# Usage: deploy.sh [home-assistant]
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

TARGET="${1:-home-assistant}"
[[ "$TARGET" == "home-assistant" ]] || die "Invalid target: '$TARGET'. Only 'home-assistant' is valid."

# -------- Paths --------
REPO_DIR="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-/etc/sops/age/keys.txt}"
export SOPS_AGE_KEY_FILE

LIVE_HUB="/home/home-assistant/home-assistant-hub"
LIVE_COMPOSE_DIR="${LIVE_HUB}/compose"
LIVE_CONFIG_DIR="${LIVE_HUB}/config"
LIVE_DATA_DIR="${LIVE_HUB}/data"
HA_CONFIG_DIR="${LIVE_DATA_DIR}/homeassistant"   # Home Assistant's /config
MOSQUITTO_CONFIG_DIR="${LIVE_CONFIG_DIR}/mosquitto"
Z2M_DATA_DIR="${LIVE_DATA_DIR}/zigbee2mqtt"
HA_URL="http://127.0.0.1:8123"

REPO_HOST_DIR="${REPO_DIR}/hosts/home-assistant"
REPO_COMPOSE="${REPO_HOST_DIR}/compose/home-assistant.yml"
REPO_HA_CONFIG="${REPO_HOST_DIR}/config/homeassistant/configuration.yaml"
REPO_MOSQUITTO_DIR="${REPO_HOST_DIR}/config/mosquitto"
REPO_Z2M_CONFIG="${REPO_HOST_DIR}/config/zigbee2mqtt/configuration.yaml"
REPO_ZIGBEE_ENV="${REPO_HOST_DIR}/config/zigbee.env"
REPO_SOPS_ENV="${REPO_HOST_DIR}/secrets/home-assistant.env.sops"

# The users the containers run as (see the compose file).
MOSQUITTO_UID=1883
Z2M_UID=1884

REQUIRED_KEYS=(MQTT_HA_PASSWORD MQTT_Z2M_PASSWORD Z2M_FRONTEND_AUTH_TOKEN ZIGBEE_NETWORK_KEY)

log_info "REPO_DIR=${REPO_DIR}"

# -------- Pre-flight --------
require_cmd sops
require_cmd docker
require_cmd sudo
require_cmd curl
sudo -n /usr/bin/docker ps >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/docker"
sudo -n /usr/bin/install --version >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/install"
[[ -r "${SOPS_AGE_KEY_FILE}" ]] || die "sops age key not readable: ${SOPS_AGE_KEY_FILE}"
for f in "${REPO_COMPOSE}" "${REPO_HA_CONFIG}" "${REPO_MOSQUITTO_DIR}/mosquitto.conf" \
         "${REPO_MOSQUITTO_DIR}/acl" "${REPO_Z2M_CONFIG}" "${REPO_ZIGBEE_ENV}" "${REPO_SOPS_ENV}"; do
  [[ -f "$f" ]] || die "missing: $f"
done
[[ -d "${LIVE_HUB}" ]] || die "missing live hub: ${LIVE_HUB} (run iac/ansible site.yml)"

git -C "$REPO_DIR" show --name-only --pretty="format:%h %s" -1 || true
echo

# -------- Zigbee settings --------
zigbee_setting() { sed -n "s/^$1=//p" "${REPO_ZIGBEE_ENV}" | tail -n 1; }
ZIGBEE_COORDINATOR=$(zigbee_setting ZIGBEE_COORDINATOR)
ZIGBEE_CHANNEL=$(zigbee_setting ZIGBEE_CHANNEL)
ZIGBEE_PAN_ID=$(zigbee_setting ZIGBEE_PAN_ID)
ZIGBEE_EXT_PAN_ID=$(zigbee_setting ZIGBEE_EXT_PAN_ID)
COMPOSE_PROFILE_ARGS=()
if [[ -n "${ZIGBEE_COORDINATOR}" ]]; then
  [[ "${ZIGBEE_COORDINATOR}" =~ ^[A-Za-z0-9.-]+:[0-9]+$ ]] || die "ZIGBEE_COORDINATOR must be host:port"
  [[ "${ZIGBEE_CHANNEL}" =~ ^(1[1-9]|2[0-6])$ ]] \
    || die "set ZIGBEE_CHANNEL (11-26; 15, 20 or 25 recommended) before Zigbee2MQTT's first start"
  [[ "${ZIGBEE_PAN_ID}" =~ ^0x[0-9a-fA-F]{4}$ ]] || die "ZIGBEE_PAN_ID must look like 0x1a62"
  [[ "${ZIGBEE_EXT_PAN_ID}" =~ ^\[([0-9]{1,3},\ ){7}[0-9]{1,3}\]$ ]] || die "ZIGBEE_EXT_PAN_ID must be a list of 8 bytes"
  COMPOSE_PROFILE_ARGS=(--profile zigbee)
  log_info "Zigbee2MQTT on: radio at ${ZIGBEE_COORDINATOR}, channel ${ZIGBEE_CHANNEL}"
else
  log_info "Zigbee2MQTT off: config/zigbee.env doesn't name the radio yet"
fi
zigbee_on() { (( ${#COMPOSE_PROFILE_ARGS[@]} > 0 )); }

compose() { (cd "${LIVE_COMPOSE_DIR}" && sudo /usr/bin/docker compose "${COMPOSE_PROFILE_ARGS[@]}" "$@"); }

# install -C copies only when content, owner or mode differ, and -v says so; the output
# tells the caller whether a running container needs a reload.
install_changed() { sudo /usr/bin/install -C -v "$@"; }

# -------- Self-heal runner ACLs --------
# The runner checks for files that only get created when missing, so it may look into the
# data dirs. It can already run docker as root, so this grants nothing new.
ensure_runner_acls() {
  log_info "Self-heal runner ACLs"
  sudo /usr/bin/setfacl -m "u:gitlab-runner:rx" "/home/home-assistant" "${LIVE_HUB}"
  sudo /usr/bin/setfacl -m "u:gitlab-runner:rwx" "${LIVE_COMPOSE_DIR}"
  sudo /usr/bin/setfacl -d -m "u:gitlab-runner:rwx" "${LIVE_COMPOSE_DIR}"
}

ensure_dirs() {
  log_info "Ensuring config and data dirs"
  # Home Assistant runs as root in its container; .storage holds its auth tokens.
  sudo /usr/bin/install -d -m 0700 -o root -g root "${HA_CONFIG_DIR}"
  # Mosquitto refuses world-readable password and ACL files, and wants them to be its own.
  sudo /usr/bin/install -d -m 0750 -o "${MOSQUITTO_UID}" -g "${MOSQUITTO_UID}" \
    "${MOSQUITTO_CONFIG_DIR}" "${LIVE_DATA_DIR}/mosquitto"
  sudo /usr/bin/install -d -m 0700 -o "${Z2M_UID}" -g "${Z2M_UID}" "${Z2M_DATA_DIR}"
  # install -d resets the mode, which is also the ACL mask, so these come after it.
  sudo /usr/bin/setfacl -m "u:gitlab-runner:rx" "${LIVE_DATA_DIR}" "${HA_CONFIG_DIR}" "${Z2M_DATA_DIR}"
}

# -------- Compose file + images --------
sync_compose() {
  log_info "Syncing the compose file and pulling images"
  sudo /usr/bin/install -m 0644 -o root -g root "${REPO_COMPOSE}" "${LIVE_COMPOSE_DIR}/docker-compose.yml"
  compose pull --quiet
}

# -------- Home Assistant config --------
# check_config runs in a throwaway container on a copy, so a broken configuration.yaml
# fails this job and never reaches the running Home Assistant.
check_ha_config() {
  log_info "Checking configuration.yaml with Home Assistant's check_config"
  local image dir out
  # --images also lists the images of the services it depends on, in random order. grep
  # reads all of it: stopping at the first match would SIGPIPE compose under pipefail.
  image=$(compose config --images homeassistant | grep -E '/home-assistant:[^/]+$') \
    || die "no Home Assistant image in the compose file"
  dir=$(mktemp -d)
  cp "${REPO_HA_CONFIG}" "${dir}/configuration.yaml"
  echo '[]' > "${dir}/automations.yaml"
  : > "${dir}/scripts.yaml"
  : > "${dir}/scenes.yaml"
  # An invalid integration setting is only a "warning" to check_config, which still exits
  # 0 for it, hence --fail-on-warnings.
  if ! out=$(sudo /usr/bin/docker run --rm --network none --cap-drop ALL --user "$(id -u):$(id -g)" \
               -v "${dir}:/config" --entrypoint python3 "${image}" \
               -m homeassistant --script check_config --config /config --fail-on-warnings 2>&1); then
    rm -rf "${dir}"
    echo "${out}"
    die "configuration.yaml failed check_config; nothing was changed"
  fi
  rm -rf "${dir}"
}

sync_ha_config() {
  log_info "Syncing Home Assistant's configuration.yaml"
  HA_CONFIG_CHANGED=$(install_changed -m 0644 -o root -g root "${REPO_HA_CONFIG}" "${HA_CONFIG_DIR}/configuration.yaml")
  # Created only when missing: the UI editors own these files.
  local f tmp
  tmp=$(mktemp)
  for f in automations.yaml scripts.yaml scenes.yaml; do
    if [[ ! -e "${HA_CONFIG_DIR}/${f}" ]]; then
      if [[ "$f" == automations.yaml ]]; then echo '[]' > "${tmp}"; else : > "${tmp}"; fi
      sudo /usr/bin/install -m 0644 -o root -g root "${tmp}" "${HA_CONFIG_DIR}/${f}"
      log_info "created ${f}"
    fi
  done
  rm -f "${tmp}"
}

sync_mosquitto_config() {
  log_info "Syncing Mosquitto's config and ACL"
  MOSQUITTO_CONF_CHANGED=$(install_changed -m 0640 -o "${MOSQUITTO_UID}" -g "${MOSQUITTO_UID}" \
    "${REPO_MOSQUITTO_DIR}/mosquitto.conf" "${MOSQUITTO_CONFIG_DIR}/mosquitto.conf")
  install_changed -m 0640 -o "${MOSQUITTO_UID}" -g "${MOSQUITTO_UID}" \
    "${REPO_MOSQUITTO_DIR}/acl" "${MOSQUITTO_CONFIG_DIR}/acl" >/dev/null
}

# -------- Secrets --------
# Decrypted into a private temp dir that is removed on exit. From it come the compose .env
# (root 0600, used by the checks below), Mosquitto's hashed password file and
# Zigbee2MQTT's secret.yaml. Nothing is printed.
render_secrets() {
  log_info "Decrypting secrets -> .env, Mosquitto passwd, Zigbee2MQTT secret.yaml"
  Z2M_SECRET_CHANGED=$(
    umask 077
    set +x
    tmp=$(mktemp -d)
    trap 'rm -rf "${tmp}"' EXIT
    sops -d --input-type dotenv --output-type dotenv "${REPO_SOPS_ENV}" > "${tmp}/secrets.env"
    for key in "${REQUIRED_KEYS[@]}"; do
      grep -qE "^${key}=." "${tmp}/secrets.env" || die "decrypted env is missing ${key}" >&2
    done
    secret() { sed -n "s/^$1=//p" "${tmp}/secrets.env"; }
    sudo /usr/bin/install -m 0600 -o root -g root "${tmp}/secrets.env" "${LIVE_COMPOSE_DIR}/.env"

    # mosquitto_passwd -U hashes the file in place. It runs from the pinned image, as the
    # runner, so it can write the runner's temp dir; new salts each time, so the broker is
    # always sent a reload.
    printf 'homeassistant:%s\nzigbee2mqtt:%s\n' "$(secret MQTT_HA_PASSWORD)" "$(secret MQTT_Z2M_PASSWORD)" > "${tmp}/passwd"
    compose run --rm --no-deps -T --user "$(id -u):$(id -g)" -v "${tmp}:/work" \
      --entrypoint mosquitto_passwd mosquitto -U /work/passwd >&2
    grep -q '^homeassistant:\$7\$' "${tmp}/passwd" || die "mosquitto_passwd didn't hash the password file" >&2
    sudo /usr/bin/install -m 0600 -o "${MOSQUITTO_UID}" -g "${MOSQUITTO_UID}" "${tmp}/passwd" "${MOSQUITTO_CONFIG_DIR}/passwd"

    if zigbee_on; then
      printf 'mqtt_password: %s\nnetwork_key: [%s]\nfrontend_auth_token: %s\n' \
        "$(secret MQTT_Z2M_PASSWORD)" "$(secret ZIGBEE_NETWORK_KEY | sed 's/,/, /g')" \
        "$(secret Z2M_FRONTEND_AUTH_TOKEN)" > "${tmp}/secret.yaml"
      install_changed -m 0600 -o "${Z2M_UID}" -g "${Z2M_UID}" "${tmp}/secret.yaml" "${Z2M_DATA_DIR}/secret.yaml"
    fi
  )
}

# Zigbee2MQTT writes to its configuration.yaml, so it's seeded once and then left alone.
seed_z2m_config() {
  zigbee_on || return 0
  if [[ -e "${Z2M_DATA_DIR}/configuration.yaml" ]]; then
    log_info "Zigbee2MQTT configuration.yaml exists; leaving it to Zigbee2MQTT"
    return 0
  fi
  log_info "Seeding Zigbee2MQTT's configuration.yaml"
  local tmp
  tmp=$(mktemp)
  sed -e "s|@ZIGBEE_COORDINATOR@|${ZIGBEE_COORDINATOR}|" \
      -e "s|@ZIGBEE_CHANNEL@|${ZIGBEE_CHANNEL}|" \
      -e "s|@ZIGBEE_PAN_ID@|${ZIGBEE_PAN_ID}|" \
      -e "s|@ZIGBEE_EXT_PAN_ID@|${ZIGBEE_EXT_PAN_ID}|" \
      "${REPO_Z2M_CONFIG}" > "${tmp}"
  ! grep -q '@[A-Z_]*@' "${tmp}" || { rm -f "${tmp}"; die "unfilled placeholder in the Zigbee2MQTT config"; }
  sudo /usr/bin/install -m 0600 -o "${Z2M_UID}" -g "${Z2M_UID}" "${tmp}" "${Z2M_DATA_DIR}/configuration.yaml"
  rm -f "${tmp}"
}

# -------- Compose up --------
compose_up() {
  log_info "docker compose up -d"
  # The project has its own name (name: home-assistant), so --remove-orphans only touches
  # this stack.
  compose up -d --remove-orphans
  compose ps
}

# compose up leaves a container alone when only a mounted file changed. Anything that was
# already running before this deploy gets the reload its changes need.
started_before_deploy() {
  local started
  started=$(sudo /usr/bin/docker inspect -f '{{.State.StartedAt}}' "$1" 2>/dev/null) || return 1
  (( $(date -d "${started}" +%s) < DEPLOY_START ))
}

reload_changed() {
  if started_before_deploy home-assistant-mosquitto; then
    if [[ -n "${MOSQUITTO_CONF_CHANGED}" ]]; then
      log_info "mosquitto.conf changed: restarting Mosquitto"
      sudo /usr/bin/docker restart home-assistant-mosquitto >/dev/null
    else
      log_info "Reloading Mosquitto's password file and ACL"
      sudo /usr/bin/docker kill -s HUP home-assistant-mosquitto >/dev/null
    fi
  fi
  if [[ -n "${HA_CONFIG_CHANGED}" ]] && started_before_deploy home-assistant; then
    log_info "configuration.yaml changed: restarting Home Assistant"
    sudo /usr/bin/docker restart home-assistant >/dev/null
  fi
  if zigbee_on && [[ -n "${Z2M_SECRET_CHANGED}" ]] && started_before_deploy home-assistant-zigbee2mqtt; then
    log_info "Zigbee2MQTT's secrets changed: restarting it"
    sudo /usr/bin/docker restart home-assistant-zigbee2mqtt >/dev/null
  fi
}

# -------- Checks --------
check_mqtt() {
  log_info "Checking Mosquitto: the homeassistant user gets in, anonymous clients don't"
  local i
  for i in $(seq 1 15); do
    if sudo /usr/bin/docker exec --env-file "${LIVE_COMPOSE_DIR}/.env" home-assistant-mosquitto \
         sh -c 'mosquitto_pub -h 127.0.0.1 -u homeassistant -P "$MQTT_HA_PASSWORD" -t deploy/check -n' 2>/dev/null; then
      break
    fi
    (( i < 15 )) || die "the homeassistant user can't log in to Mosquitto"
    sleep 2
  done
  if sudo /usr/bin/docker exec home-assistant-mosquitto mosquitto_pub -h 127.0.0.1 -t deploy/check -n 2>/dev/null; then
    die "Mosquitto accepted an anonymous client"
  fi
}

wait_ha_ready() {
  log_info "Waiting for Home Assistant on ${HA_URL} (up to 5 min)"
  local i
  for i in $(seq 1 60); do
    if [[ "$(curl -s -o /dev/null -w '%{http_code}' -m 5 "${HA_URL}/manifest.json" || true)" == 200 ]]; then
      log_success "Home Assistant is up"
      # The first visitor to an un-onboarded Home Assistant creates its owner account.
      if curl -fsS -m 5 "${HA_URL}/api/onboarding" 2>/dev/null | grep -Eq '"step": ?"user", ?"done": ?false'; then
        log_warn "Home Assistant has no owner yet: onboard it now at http://10.0.1.12:8123"
      fi
      return 0
    fi
    sleep 5
  done
  sudo /usr/bin/docker logs --tail 40 home-assistant 2>&1 || true
  die "Home Assistant did not answer within 5 minutes"
}

log_info "========================================"
log_info "Home Assistant deployment"
log_info "========================================"
ensure_runner_acls
ensure_dirs
sync_compose
check_ha_config
sync_ha_config
sync_mosquitto_config
render_secrets
seed_z2m_config
DEPLOY_START=$(date +%s)
compose_up
reload_changed
check_mqtt
wait_ha_ready
if zigbee_on; then
  sudo /usr/bin/docker logs --tail 15 home-assistant-zigbee2mqtt 2>&1 || true
fi
log_success "Home Assistant deployed"
