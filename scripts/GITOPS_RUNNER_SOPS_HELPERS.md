# GitOps Helper Scripts (Runner Permissions + Safe Sync + SOPS Env Encryption)

**Last updated:** 2026-02-07 (America/Denver)

These are the small “sharp tools” that make our GitLab Runner + SOPS workflow safer and more repeatable.
This doc provides **inline comments** plus **usage** for each script so it can live directly in GitLab docs.

> **Security note:** These tools deliberately add guardrails to reduce blast radius (path allowlists, minimal sudo, strict file perms).

---

## 1) `root-wrapper.sh` → installs `/usr/local/sbin/gitops-sync`

### What it is
A root-only installer that writes a tiny wrapper executable, `gitops-sync`, which performs a safe rsync from:
- **SRC:** a GitLab Runner build directory (`/home/gitlab-runner/builds/*`)
- **DST:** a service “hub” directory under `/home/*/*-hub/*`

This lets deploy scripts sync repo-managed compose/config into live hubs without allowing arbitrary rsync to arbitrary paths.

### Why it exists
If you allow rsync freely under sudo, you’ve basically given the runner a file-writer primitive across the host.
This wrapper narrows the allowed paths and makes auditing easier.

### Usage
Install once (as root):
```bash
sudo bash root-wrapper.sh
```

Then in deploy scripts (typically under sudo allowlist):
```bash
/usr/local/sbin/gitops-sync "$SRC_DIR" "$DST_DIR"
```

Examples:
```bash
/usr/local/sbin/gitops-sync \
  "$CI_PROJECT_DIR/hosts/traefik/compose" \
  "/home/traefik/traefik-hub/compose"
```

### Exit codes
- `2` usage / missing src/dst dir
- `3` refused SRC (not in runner builds)
- `4` refused DST (not under /home/*/*-hub/*)
- other: rsync exit code

### Commented version (reference)
```bash
#!/usr/bin/env bash
# root-wrapper.sh
#
# Installs /usr/local/sbin/gitops-sync with strict safety rails.
# This script should be executed as root once per host.

set -euo pipefail

# Write the gitops-sync helper atomically
cat > /usr/local/sbin/gitops-sync <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

SRC="${1:-}"
DST="${2:-}"

usage() { echo "usage: gitops-sync <src_dir> <dst_dir>" >&2; exit 2; }

# Basic arg + existence checks
[[ -n "$SRC" && -n "$DST" ]] || usage
[[ -d "$SRC" ]] || { echo "missing src dir: $SRC" >&2; exit 2; }
[[ -d "$DST" ]] || { echo "missing dst dir: $DST" >&2; exit 2; }

# Safety rails:
# - only allow syncing OUT of a gitlab-runner build dir
# - only allow syncing INTO a service hub location
case "$SRC" in
  /home/gitlab-runner/builds/*) ;;
  *) echo "refusing SRC (not in runner builds): $SRC" >&2; exit 3 ;;
esac

case "$DST" in
  /home/*/*-hub/*) ;;
  *) echo "refusing DST (not under /home/*/*-hub/*): $DST" >&2; exit 4 ;;
esac

# Perform the sync with delete to keep runtime matching repo
exec /usr/bin/rsync -a --delete "$SRC/" "$DST/"
EOF

chmod 0755 /usr/local/sbin/gitops-sync
chown root:root /usr/local/sbin/gitops-sync

# Quick check (expected to fail but validates install path)
# Note: /tmp is not an allowed SRC, so this should be refused.
 /usr/local/sbin/gitops-sync /tmp /tmp >/dev/null 2>&1 || true

echo "gitops-sync installed: $(ls -l /usr/local/sbin/gitops-sync)"
```

---

## 2) `gitlab-runner-deploy-bootstrap.sh` (runner + SOPS + ACL + sudoers baseline)

### What it is
A one-shot bootstrap script that:
1) ensures the **age key directory** exists with correct perms
2) enforces **age key file perms** so the runner can decrypt but non-root can’t
3) installs a **tight sudoers allowlist** for deploy actions
4) discovers live compose dirs under `/home/*/*-hub/compose` and applies ACLs so runner can traverse + write where needed
5) prints verification output (age perms, sudo -l, sample ACLs)

### Usage
Run as root on the target host:
```bash
sudo bash gitlab-runner-deploy-bootstrap.sh
```

Override defaults if needed:
```bash
sudo RUNNER_USER=gitlab-runner \
     RUNNER_GROUP=gitlab-runner \
     AGE_DIR=/etc/sops/age \
     AGE_KEYS=/etc/sops/age/keys.txt \
     SUDOERS_FILE=/etc/sudoers.d/gitlab-runner-deploy \
     HOME_BASE=/home \
     STACK_GLOB="/home/*/*-hub/compose" \
     bash gitlab-runner-deploy-bootstrap.sh
```

### Key behaviors to understand
- Uses ACLs for runner access **without** putting people in the `docker` group.
- Uses `nullglob` so if no compose dirs exist, it won’t treat the glob literally.
- Sudoers keeps env vars needed by deploy scripts via `env_keep`.

### Commented version (reference)
```bash
#!/usr/bin/env bash
set -euo pipefail

# ---- tunables (with sensible defaults) ----
RUNNER_USER="${RUNNER_USER:-gitlab-runner}"
RUNNER_GROUP="${RUNNER_GROUP:-gitlab-runner}"

AGE_DIR="${AGE_DIR:-/etc/sops/age}"
AGE_KEYS="${AGE_KEYS:-${AGE_DIR}/keys.txt}"

SUDOERS_FILE="${SUDOERS_FILE:-/etc/sudoers.d/gitlab-runner-deploy}"

# Where to look for stacks (service hub convention)
HOME_BASE="${HOME_BASE:-/home}"
STACK_GLOB="${STACK_GLOB:-${HOME_BASE}/*/*-hub/compose}"

# ---- helpers ----
log() { echo "[$(date -Is)] $*"; }

apply_traverse_acl() {
  local path="$1"
  local p="$path"

  # Ensure runner can traverse every parent directory down to /home (inclusive).
  while [[ "$p" != "/" ]]; do
    # Don't fail the whole run if a parent ACL can't be set (e.g., immutable FS).
    setfacl -m "u:${RUNNER_USER}:x" "$p" 2>/dev/null || true
    p="$(dirname "$p")"
  done
}

apply_compose_acl() {
  local d="$1"
  # Runner needs RWX on compose dir + default ACL so new files inherit.
  setfacl -m  "u:${RUNNER_USER}:rwx" "$d"
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
Cmnd_Alias DEPLOY_CMDS = /usr/bin/docker, /usr/bin/install, /bin/mkdir, /bin/chmod, /bin/chown

gitlab-runner ALL=(root) NOPASSWD: DEPLOY_CMDS
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
  [[ -d "$d" ]] || continue

  log "Applying ACLs to: $d"
  apply_traverse_acl "$d"
  apply_compose_acl "$d"
done

# ---- 4) verify summary ----
log "Verify: age perms"
ls -ld "${AGE_DIR}"
ls -l  "${AGE_KEYS}"

log "Verify: sudo -l (first ~120 lines)"
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
```

---

## 3) `sops_env_encrypt` helper (encrypt a plaintext `.env` into `*.env.sops`)

### What it is
A helper function/script that takes:
- a plaintext dotenv file (e.g., a live `.env` from a service hub)
- a repo-relative target path ending in `.sops`

…and encrypts it using **SOPS creation_rules**, ensuring the correct recipients are selected.

### Why it exists
SOPS creation rules match against the **output filename** (or `--filename-override`), not the input file.
If you do something like `sops -e /path/to/.env > hosts/x/secrets/compose.env.sops`, SOPS may not be able to match the rule correctly.

This helper forces the match using:
- `--filename-override "$target"`
- `--output "$target"`

### Usage
As a function (sourced) or as a standalone script:

```bash
# encrypt a service's live .env into the repo secrets file
sops_env_encrypt /home/wiki/wiki-hub/compose/.env \
  hosts/servicehub/secrets/compose.env.sops
```

### Requirements
- `sops` in PATH
- `.sops.yaml` configured with matching creation_rules for `*.env.sops` targets

### Commented version (reference)
```bash
sops_env_encrypt() {
  # Encrypt a dotenv plaintext file into a SOPS-encrypted dotenv.
  #
  # Usage:
  #   sops_env_encrypt <PLAINTEXT_ENV> <TARGET_ENV_SOPS>
  #
  # Example:
  #   sops_env_encrypt /home/wiki/wiki-hub/compose/.env \
  #     hosts/servicehub/secrets/compose.env.sops

  set -euo pipefail

  # ---- args ----
  if [[ $# -ne 2 ]]; then
    cat >&2 <<'EOF'
Usage:  sops_env_encrypt <PLAINTEXT_ENV> <TARGET_ENV_SOPS>

Encrypt a dotenv file using SOPS, applying .sops.yaml creation_rules
via --filename-override.

Arguments:
  PLAINTEXT_ENV      Path to plaintext .env file
  TARGET_ENV_SOPS    Repo-relative path ending in .env.sops

Example:
  sops_env_encrypt /home/wiki/wiki-hub/compose/.env \
    hosts/servicehub/secrets/compose.env.sops
EOF
    return 2
  fi

  local plaintext="$1"
  local target="$2"

  # ---- sanity checks ----
  command -v sops >/dev/null 2>&1 || { echo "ERROR: sops not found in PATH" >&2; return 127; }
  [[ -f "$plaintext" ]] || { echo "ERROR: plaintext file not found: $plaintext" >&2; return 3; }
  [[ -r "$plaintext" ]] || { echo "ERROR: plaintext file not readable: $plaintext" >&2; return 4; }

  local target_dir
  target_dir="$(dirname "$target")"
  [[ -d "$target_dir" ]] || { echo "ERROR: target directory does not exist: $target_dir" >&2; return 5; }

  [[ "$target" == *.sops ]] || { echo "ERROR: target must end in .sops (got: $target)" >&2; return 6; }

  # ---- encrypt ----
  # IMPORTANT:
  # - filename-override is what creation_rules match against
  # - input path is ignored for rule matching
  #
  # We do NOT echo secrets or enable xtrace here.
  umask 077

  sops --encrypt \
    --input-type dotenv \
    --output-type dotenv \
    --filename-override "$target" \
    --output "$target" \
    "$plaintext"

  echo "✓ Encrypted: $target"
}

# If used as a script instead of a sourced function
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  sops_env_encrypt "$@"
fi
```

---

