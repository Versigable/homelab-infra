#!/usr/bin/env bash
set -euo pipefail

RUNNER_USER="${RUNNER_USER:-gitlab-runner}"
RUNNER_GROUP="${RUNNER_GROUP:-gitlab-runner}"

AGE_DIR="${AGE_DIR:-/etc/sops/age}"
AGE_KEYS="${AGE_KEYS:-${AGE_DIR}/keys.txt}"

SUDOERS_FILE="${SUDOERS_FILE:-/etc/sudoers.d/gitlab-runner-deploy}"

# Where to look for stacks
HOME_BASE="${HOME_BASE:-/home}"
STACK_GLOB="${STACK_GLOB:-${HOME_BASE}/*/*-hub/compose}"

# ---- helpers ----
log() { echo "[$(date -Is)] $*"; }

apply_traverse_acl() {
  local path="$1"
  local p="$path"

  # Ensure runner can traverse every parent directory down to /home (inclusive)
  while [[ "$p" != "/" ]]; do
    # Don't fail the whole run if a parent ACL can't be set (e.g., immutable FS)
    setfacl -m "u:${RUNNER_USER}:rx" "$p" 2>/dev/null || true
    p="$(dirname "$p")"
  done
}

apply_compose_acl() {
  local d="$1"
  setfacl -m "u:${RUNNER_USER}:rwx" "$d"
  setfacl -d -m "u:${RUNNER_USER}:rwx" "$d"
}

# ---- 1) age key perms (Traefik standard) ----
log "Setting age key permissions"
install -d -m 0750 -o root -g "${RUNNER_GROUP}" "${AGE_DIR}"

if [[ ! -f "${AGE_KEYS}" ]]; then
  log "ERROR: ${AGE_KEYS} does not exist. Create/generate age keys first."
  exit 1
fi

chown root:"${RUNNER_GROUP}" "${AGE_KEYS}"
chmod 0640 "${AGE_KEYS}"

# ---- 2) sudoers (tight, like Traefik) ----
log "Installing sudoers at ${SUDOERS_FILE}"
cat > "${SUDOERS_FILE}" <<'EOF'
# Allow gitlab-runner to perform deploy actions non-interactively (NO password).
Defaults:gitlab-runner !requiretty
Defaults:gitlab-runner secure_path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Defaults:gitlab-runner env_keep += "SOPS_AGE_KEY_FILE CI_PROJECT_DIR LIVE_STACK_DIR LIVE_ENV_FILE"

# Keep this list tight. Add only what deploy.sh needs.
# /usr/bin/setfacl: deploy.sh self-heals runner traversal/write ACLs on home/hub/live dirs
#                   so onboarding a new ServiceHub stack no longer requires a separate runner-perms.sh run.
Cmnd_Alias DEPLOY_CMDS = /usr/bin/docker, /usr/bin/install, /bin/mkdir, /bin/chmod, /bin/chown, /usr/bin/setfacl

# Scripts shipped from the repo's scripts/ dir, installed by deploy.sh into a stable location.
# Path is fixed (no wildcards) so sudoers stays auditable.
Cmnd_Alias DEPLOY_SCRIPTS = /opt/homelab-infra/scripts/devops-grant.sh

gitlab-runner ALL=(root) NOPASSWD: DEPLOY_CMDS, DEPLOY_SCRIPTS
EOF

chmod 0440 "${SUDOERS_FILE}"
visudo -cf "${SUDOERS_FILE}" >/dev/null
log "sudoers OK"

# ---- 3) discover compose dirs and apply ACLs ----
log "Discovering live stack compose dirs: ${STACK_GLOB}"

# Use nullglob so unmatched globs disappear instead of staying literal
shopt -s nullglob
compose_dirs=( ${STACK_GLOB} )
shopt -u nullglob

if (( ${#compose_dirs[@]} == 0 )); then
  log "WARN: No compose dirs found matching ${STACK_GLOB}"
else
  log "Found ${#compose_dirs[@]} compose dir(s)"
fi

for d in "${compose_dirs[@]}"; do
  # Only real directories
  [[ -d "$d" ]] || continue

  log "Applying ACLs to: $d"
  apply_traverse_acl "$d"
  apply_compose_acl "$d"
done

# ---- 4) verify summary ----
log "Verify: age perms"
ls -ld "${AGE_DIR}"
ls -l "${AGE_KEYS}"

log "Verify: sudo -l for runner (first ~120 lines)"
sudo -l -U "${RUNNER_USER}" | sed -n '1,120p' || true

log "Verify: sample ACLs (first 3 dirs)"
count=0
for d in "${compose_dirs[@]}"; do
  [[ -d "$d" ]] || continue
  getfacl -p "$d" | sed -n '1,40p'
  echo
  ((count++))
  [[ "$count" -ge 3 ]] && break
done

log "Done."
