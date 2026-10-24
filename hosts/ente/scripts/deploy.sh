#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# deploy.sh (Ente Photos) — Model B GitOps
#
# Deploys Ente (museum, web, Postgres, Garage) to /home/ente/ente-hub on VM 115.
# There is no manual bootstrap: the VM, its runner, the runner's sudoers, the
# SOPS age key and the directory layout all come from iac/ (Terraform + Ansible).
# Garage's layout, the museum's S3 key, the bucket and its CORS rules are set up
# here, and each step is skipped when it's already in place.
#
# Refuses to run unless the TrueNAS share is mounted at /srv/ente-data. Without it
# Garage would put the encrypted photos on the VM's root disk.
#
# Usage: deploy.sh [ente]
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

TARGET="${1:-ente}"
[[ "$TARGET" == "ente" ]] || die "Invalid target: '$TARGET'. Only 'ente' is valid."

# -------- Paths --------
REPO_DIR="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-/etc/sops/age/keys.txt}"
export SOPS_AGE_KEY_FILE

LIVE_HUB="/home/ente/ente-hub"
LIVE_COMPOSE_DIR="${LIVE_HUB}/compose"
LIVE_CONFIG_DIR="${LIVE_HUB}/config"
DATA_MOUNT="/srv/ente-data"
PING_URL="http://10.0.1.15:8080/ping"
BUCKET="b2-eu-cen"
S3_KEY_NAME="ente-museum"
# Pinned like the stack's images; only used to set the bucket's CORS rules.
AWS_CLI_IMAGE="amazon/aws-cli:2.37.11"

REPO_HOST_DIR="${REPO_DIR}/hosts/ente"
REPO_COMPOSE="${REPO_HOST_DIR}/compose/ente.yml"
REPO_MUSEUM="${REPO_HOST_DIR}/config/museum.yaml"
REPO_GARAGE="${REPO_HOST_DIR}/config/garage.toml"
REPO_CORS="${REPO_HOST_DIR}/config/cors.json"
REPO_SOPS_ENV="${REPO_HOST_DIR}/secrets/ente.env.sops"

# Keys the compose file interpolates; deploy stops if any is missing after decrypt.
REQUIRED_KEYS=(POSTGRES_PASSWORD S3_KEY_ID S3_SECRET ENTE_KEY_ENCRYPTION ENTE_KEY_HASH
               ENTE_JWT_SECRET GARAGE_RPC_SECRET GARAGE_ADMIN_TOKEN)

log_info "REPO_DIR=${REPO_DIR}"

# -------- Pre-flight --------
require_cmd sops
require_cmd docker
require_cmd sudo
require_cmd curl
require_cmd stat
sudo -n /usr/bin/docker ps >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/docker"
sudo -n /usr/bin/install --version >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/install"
[[ -r "${SOPS_AGE_KEY_FILE}" ]] || die "sops age key not readable: ${SOPS_AGE_KEY_FILE}"
for f in "${REPO_COMPOSE}" "${REPO_MUSEUM}" "${REPO_GARAGE}" "${REPO_CORS}" "${REPO_SOPS_ENV}"; do
  [[ -f "$f" ]] || die "missing: $f"
done
[[ -d "${LIVE_HUB}" ]] || die "missing live hub: ${LIVE_HUB} (run iac/ansible site.yml)"
# An automount point counts as a mountpoint even when the share failed to mount, so check
# the filesystem type; reading the path is what triggers the mount.
[[ "$(stat -f -c %T "${DATA_MOUNT}/." 2>/dev/null)" == nfs ]] \
  || die "${DATA_MOUNT} is not the TrueNAS share. Check nfs_client_mounts in iac/ansible host_vars/ente.yml and the share's hosts."

git -C "$REPO_DIR" show --name-only --pretty="format:%h %s" -1 || true
echo

# -------- Self-heal runner ACLs --------
ensure_runner_acls() {
  log_info "Self-heal runner ACLs"
  sudo /usr/bin/setfacl -m "u:gitlab-runner:rx" "/home/ente" "${LIVE_HUB}"
  sudo /usr/bin/setfacl -m "u:gitlab-runner:rwx" "${LIVE_COMPOSE_DIR}"
  sudo /usr/bin/setfacl -d -m "u:gitlab-runner:rwx" "${LIVE_COMPOSE_DIR}"
}

# -------- Sync compose + config --------
sync_files() {
  log_info "Syncing compose, museum.yaml, garage.toml and the CORS rules"
  sudo /usr/bin/install -m 0644 -o root -g root "${REPO_COMPOSE}" "${LIVE_COMPOSE_DIR}/docker-compose.yml"
  sudo /usr/bin/install -d -m 0755 -o root -g root "${LIVE_CONFIG_DIR}"
  sudo /usr/bin/install -m 0644 -o root -g root "${REPO_MUSEUM}" "${LIVE_CONFIG_DIR}/museum.yaml"
  sudo /usr/bin/install -m 0644 -o root -g root "${REPO_GARAGE}" "${LIVE_CONFIG_DIR}/garage.toml"
  sudo /usr/bin/install -m 0644 -o root -g root "${REPO_CORS}" "${LIVE_CONFIG_DIR}/cors.json"
}

# -------- Decrypt secrets --------
decrypt_env() {
  log_info "Decrypting secrets -> ${LIVE_COMPOSE_DIR}/.env (root 0600)"
  (
    umask 077
    local tmp_env key
    tmp_env="$(mktemp)"
    trap 'rm -f "${tmp_env}"' EXIT
    set +x
    sops -d --input-type dotenv --output-type dotenv "${REPO_SOPS_ENV}" > "${tmp_env}"
    for key in "${REQUIRED_KEYS[@]}"; do
      grep -qE "^${key}=." "${tmp_env}" || die "decrypted env is missing ${key}"
    done
    sudo /usr/bin/install -m 0600 -o root -g root "${tmp_env}" "${LIVE_COMPOSE_DIR}/.env"
  )
}

# secret <KEY>: one value from the SOPS file, for the Garage setup. Never echoed.
secret() {
  sops -d --input-type dotenv --output-type dotenv "${REPO_SOPS_ENV}" | sed -n "s/^$1=//p"
}

# -------- Data dirs --------
ensure_data_dirs() {
  log_info "Ensuring data dirs"
  # postgres: the alpine image's postgres user is 70.
  sudo /usr/bin/install -d -m 0700 -o 70 -g 70 "${LIVE_HUB}/data/postgres"
  # Garage runs as uid 1000.
  sudo /usr/bin/install -d -m 0700 -o 1000 -g 1000 "${LIVE_HUB}/data/garage-meta"
  # On the share, Mapall makes every file entedata (uid 1000 in here), so no owner is
  # set. Never chmod or chown the share's root: the node's root owns it (Mapall) and the
  # change would reach TrueNAS.
  sudo /usr/bin/install -d -m 0700 "${DATA_MOUNT}/garage" "${DATA_MOUNT}/garage-meta-snapshots"
}

# -------- Compose up --------
compose_up() {
  log_info "docker compose pull && up -d"
  (
    cd "${LIVE_COMPOSE_DIR}"
    sudo /usr/bin/docker compose pull --quiet
    # The project has its own name (name: ente), so --remove-orphans only touches
    # this stack.
    sudo /usr/bin/docker compose up -d --remove-orphans
    sudo /usr/bin/docker compose ps
  )
}

# -------- Garage: layout, key, bucket, CORS --------
garage() { sudo /usr/bin/docker exec ente-s3 /garage "$@"; }

setup_garage() {
  log_info "Garage: waiting for the node"
  local i
  for i in $(seq 1 30); do
    garage status >/dev/null 2>&1 && break
    (( i == 30 )) && die "Garage did not come up"
    sleep 2
  done

  # A fresh node has no role. Give it one (1 TB, matching the dataset's quota).
  if garage status | grep -q 'NO ROLE ASSIGNED'; then
    log_info "Garage: assigning the node's layout"
    local node version
    node=$(garage node id -q | cut -d@ -f1)
    garage layout assign -z dc1 -c 1T "${node}"
    version=$(garage layout show | sed -n 's/.*[Cc]urrent cluster layout version: *\([0-9]*\).*/\1/p')
    garage layout apply --version $(( ${version:-0} + 1 ))
  fi

  # The key ID and secret come from SOPS, so museum's credentials survive a rebuild.
  # They're passed as arguments inside the VM only, and never printed.
  if ! garage key info "${S3_KEY_NAME}" >/dev/null 2>&1; then
    log_info "Garage: importing the museum's key"
    garage key import --yes -n "${S3_KEY_NAME}" "$(secret S3_KEY_ID)" "$(secret S3_SECRET)" >/dev/null
  fi
  if ! garage bucket info "${BUCKET}" >/dev/null 2>&1; then
    log_info "Garage: creating bucket ${BUCKET}"
    garage bucket create "${BUCKET}"
  fi
  garage bucket allow --read --write --owner "${BUCKET}" --key "${S3_KEY_NAME}" >/dev/null

  # Browsers on photos. and albums. fetch files straight from S3, so the bucket needs
  # CORS rules. Setting them again is harmless.
  log_info "Garage: applying CORS rules"
  (
    umask 077
    local tmp
    tmp="$(mktemp -d)"
    trap 'rm -rf "${tmp}"' EXIT
    set +x
    {
      echo "AWS_ACCESS_KEY_ID=$(secret S3_KEY_ID)"
      echo "AWS_SECRET_ACCESS_KEY=$(secret S3_SECRET)"
      echo "AWS_DEFAULT_REGION=garage"
      echo "AWS_CONFIG_FILE=/aws/config"
    } > "${tmp}/env"
    printf '[default]\ns3 =\n  addressing_style = path\n' > "${tmp}/config"
    chmod 0755 "${tmp}"
    chmod 0644 "${tmp}/config"
    sudo /usr/bin/docker run --rm --network ente-net --env-file "${tmp}/env" \
      -v "${tmp}/config:/aws/config:ro" -v "${LIVE_CONFIG_DIR}/cors.json:/cors.json:ro" \
      "${AWS_CLI_IMAGE}" s3api put-bucket-cors --endpoint-url http://ente-s3:3900 \
      --bucket "${BUCKET}" --cors-configuration file:///cors.json
  )
}

# -------- Wait for museum --------
wait_ready() {
  log_info "Waiting for ${PING_URL} (up to 5 min)"
  local i body
  for i in $(seq 1 60); do
    body=$(curl -fsS -m 5 "${PING_URL}" 2>/dev/null || true)
    if [[ -n "$body" ]]; then
      log_success "museum is up: ${body}"
      return 0
    fi
    sleep 5
  done
  sudo /usr/bin/docker logs --tail 40 ente-museum 2>&1 || true
  die "museum did not answer ${PING_URL} within 5 minutes"
}

log_info "========================================"
log_info "Ente deployment"
log_info "========================================"
ensure_runner_acls
sync_files
decrypt_env
ensure_data_dirs
compose_up
setup_garage
wait_ready
log_success "Ente deployed"
