#!/usr/bin/env bash
set -euo pipefail
# Several steps run inside $(...), and a failure there must stop the deploy too.
shopt -s inherit_errexit

# ============================================================
# deploy.sh (wolverine-games) — Model B GitOps
#
# Deploys one game to /home/wolverine-games/wolverine-games-hub on LXC 304 (wolverine).
# There is no manual bootstrap: the LXC, its runner, the runner's sudoers, the SOPS age
# key and the directory layout all come from iac/ (Terraform + Ansible).
#
# Each game gets compose/<game>/ (docker-compose.yml + .env), data/<game>/ and
# backups/<game>/. Secrets: secrets/<game>.env.sops, decrypted only on this host.
#
# Usage: deploy.sh <game>      (games: enshrouded)
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

VALID_GAMES=(enshrouded)
GAME="${1:-}"
[[ " ${VALID_GAMES[*]} " == *" ${GAME} "* && -n "${GAME}" ]] \
  || die "usage: deploy.sh <game>; valid games: ${VALID_GAMES[*]}"

# -------- Paths --------
REPO_DIR="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-/etc/sops/age/keys.txt}"
export SOPS_AGE_KEY_FILE

EXPECTED_HOST="wolverine-games"
LIVE_HUB="/home/wolverine-games/wolverine-games-hub"
LIVE_COMPOSE_DIR="${LIVE_HUB}/compose/${GAME}"
LIVE_DATA_DIR="${LIVE_HUB}/data/${GAME}"
LIVE_BACKUP_DIR="${LIVE_HUB}/backups/${GAME}"

REPO_COMPOSE="${REPO_DIR}/hosts/wolverine-games/compose/${GAME}.yml"
REPO_SOPS_ENV="${REPO_DIR}/hosts/wolverine-games/secrets/${GAME}.env.sops"

# -------- Per-game settings --------
case "${GAME}" in
  enshrouded)
    CONTAINER=enshrouded
    # PUID/PGID in the compose file; the game, updater and backups run as this id.
    RUN_UID=4711
    RUN_GID=4711
    QUERY_PORT=15637
    REQUIRED_KEYS=(ENSHROUDED_ADMIN_PASSWORD ENSHROUDED_FRIEND_PASSWORD)
    EXPECTED_ROLES='["Admin","Friend"]'
    # The first start downloads the game (about 4 GB) and builds the Proton prefix.
    READY_TIMEOUT_MIN=30
    ;;
esac

log_info "REPO_DIR=${REPO_DIR} GAME=${GAME}"

# -------- Pre-flight --------
require_cmd sops
require_cmd docker
require_cmd sudo
require_cmd python3
sudo -n /usr/bin/docker ps >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/docker"
sudo -n /usr/bin/install --version >/dev/null 2>&1 || die "runner needs NOPASSWD sudo for /usr/bin/install"
[[ -r "${SOPS_AGE_KEY_FILE}" ]] || die "sops age key not readable: ${SOPS_AGE_KEY_FILE}"
for f in "${REPO_COMPOSE}" "${REPO_SOPS_ENV}"; do
  [[ -f "$f" ]] || die "missing: $f"
done
[[ -d "${LIVE_HUB}" ]] || die "missing live hub: ${LIVE_HUB} (run iac/ansible site.yml)"
[[ "$(hostname)" == "${EXPECTED_HOST}" ]] || die "this deploy is for ${EXPECTED_HOST}, not $(hostname)"

git -C "$REPO_DIR" show --name-only --pretty="format:%h %s" -1 || true
echo

# --project-directory, so .env is read from the game's compose dir without a cd into a
# root-owned directory.
compose() {
  sudo /usr/bin/docker compose --project-directory "${LIVE_COMPOSE_DIR}" \
    -f "${LIVE_COMPOSE_DIR}/docker-compose.yml" "$@"
}

# -------- Directories --------
prepare_dirs() {
  log_info "Directories for ${GAME}"
  sudo /usr/bin/install -d -m 0750 -o root -g root "${LIVE_COMPOSE_DIR}"
  # Owned by the id the game runs as; the image also chowns its game dir on start.
  sudo /usr/bin/install -d -m 0750 -o "${RUN_UID}" -g "${RUN_GID}" "${LIVE_DATA_DIR}" "${LIVE_BACKUP_DIR}"
}

# -------- Secrets + compose --------
render_env() {
  log_info "Decrypting secrets -> compose/${GAME}/.env"
  (
    umask 077
    tmp=$(mktemp)
    trap 'rm -f "${tmp}"' EXIT
    sops -d --input-type dotenv --output-type dotenv "${REPO_SOPS_ENV}" > "${tmp}"
    for k in "${REQUIRED_KEYS[@]}"; do
      grep -qE "^${k}=." "${tmp}" || die "decrypted env is missing ${k}"
    done
    sudo /usr/bin/install -m 0600 -o root -g root "${tmp}" "${LIVE_COMPOSE_DIR}/.env"
  )
}

sync_compose() {
  log_info "Syncing the compose file and pulling the image"
  sudo /usr/bin/install -m 0644 -o root -g root "${REPO_COMPOSE}" "${LIVE_COMPOSE_DIR}/docker-compose.yml"
  compose config --quiet || die "compose config is invalid"
  compose pull --quiet
}

compose_up() {
  log_info "docker compose up -d"
  # The project has its own name (name: ${GAME}), so --remove-orphans only touches it.
  compose up -d --remove-orphans
  compose ps
}

# -------- Checks --------
# A Steam A2S_INFO query on the published port: answers only once the game itself is up,
# not just the container. Prints "name|players|max" on success.
a2s_info() {
  python3 - "${QUERY_PORT}" <<'PY'
import socket, struct, sys
port = int(sys.argv[1])
req = b"\xff\xff\xff\xffTSource Engine Query\x00"
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(3)
try:
    s.sendto(req, ("127.0.0.1", port))
    data, _ = s.recvfrom(4096)
    if data[4] == 0x41:  # challenge: repeat with it appended
        s.sendto(req + data[5:9], ("127.0.0.1", port))
        data, _ = s.recvfrom(4096)
    if data[4] != 0x49:
        sys.exit(1)
    p = 6
    def cstr():
        global p
        e = data.index(b"\x00", p)
        v = data[p:e].decode("utf-8", "replace")
        p = e + 1
        return v
    name = cstr(); cstr(); cstr(); cstr()
    _, players, maxp = struct.unpack_from("<HBB", data, p)
    print(f"{name}|{players}|{maxp}")
except (OSError, IndexError, struct.error):
    sys.exit(1)
PY
}

wait_ready() {
  log_info "Waiting for ${GAME} to answer Steam queries on ${QUERY_PORT}/udp (up to ${READY_TIMEOUT_MIN} min)"
  local info state restarts first_restarts next_note=120 start=${SECONDS}
  local deadline=$(( SECONDS + READY_TIMEOUT_MIN * 60 ))
  first_restarts=$(sudo /usr/bin/docker inspect -f '{{.RestartCount}}' "${CONTAINER}")
  while (( SECONDS < deadline )); do
    if info=$(a2s_info); then
      log_success "Server up after $(( (SECONDS - start) / 60 )) min: ${info%%|*} (players ${info#*|})"
      return 0
    fi
    state=$(sudo /usr/bin/docker inspect -f '{{.State.Status}}' "${CONTAINER}" 2>/dev/null || echo missing)
    restarts=$(sudo /usr/bin/docker inspect -f '{{.RestartCount}}' "${CONTAINER}" 2>/dev/null || echo 0)
    if [[ "${state}" != running ]] || (( restarts > first_restarts )); then
      sudo /usr/bin/docker logs --tail 60 "${CONTAINER}" 2>&1 || true
      die "container is ${state} after ${restarts} restarts"
    fi
    if (( SECONDS - start >= next_note )); then
      log_info "still starting ($(( (SECONDS - start) / 60 )) min): $(sudo /usr/bin/docker logs --tail 1 "${CONTAINER}" 2>&1 | cut -c1-160)"
      next_note=$(( next_note + 120 ))
    fi
    sleep 10
  done
  sudo /usr/bin/docker logs --tail 80 "${CONTAINER}" 2>&1 || true
  die "${GAME} did not answer within ${READY_TIMEOUT_MIN} minutes"
}

# The roles in the live config must be exactly the ones in git: a leftover group would be
# another way in. Prints names and permissions only, never passwords.
check_roles() {
  [[ "${GAME}" == enshrouded ]] || return 0
  log_info "Checking the server roles in enshrouded_server.json"
  local roles names
  roles=$(sudo /usr/bin/docker exec "${CONTAINER}" jq -c \
    '[.userGroups[] | {name, canKickBan, canAccessInventories, canEditWorld, canEditBase, canExtendBase, reservedSlots, hasPassword: ((.password // "") != "")}]' \
    /opt/enshrouded/enshrouded_server.json)
  echo "${roles}"
  names=$(python3 -c 'import json,sys; print(json.dumps([r["name"] for r in json.loads(sys.argv[1])], separators=(",", ":")))' "${roles}")
  [[ "${names}" == "${EXPECTED_ROLES}" ]] || die "roles are ${names}, expected ${EXPECTED_ROLES}"
  python3 -c 'import json,sys; sys.exit(0 if all(r["hasPassword"] for r in json.loads(sys.argv[1])) else 1)' "${roles}" \
    || die "a role has an empty password"
  log_success "Roles: ${names}, each with a password"
}

log_info "========================================"
log_info "${GAME} deployment"
log_info "========================================"
prepare_dirs
render_env
sync_compose
compose_up
wait_ready
check_roles
log_success "${GAME} deployed"
