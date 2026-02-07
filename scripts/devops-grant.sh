#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <repo_path> <service_home_dir>"
  echo "Example: $0 /home/valheim/valheim-hub /home/valheim"
  exit 1
fi

REPO="$1"
HOME_DIR="$2"

[[ -d "$REPO" ]] || { echo "Repo not found: $REPO" >&2; exit 2; }
[[ -d "$HOME_DIR" ]] || { echo "Home dir not found: $HOME_DIR" >&2; exit 2; }

# Resolve the service user from the home dir owner
SERVICE_USER="$(stat -c '%U' "$HOME_DIR")"

# Ensure devops group exists
if ! getent group devops >/dev/null; then
  groupadd devops
  echo "Created group 'devops'"
fi

# 1) Service home: allow devops to browse (rx) for tab-complete/VS Code
chmod 750 "$HOME_DIR"
setfacl -m g:devops:rx "$HOME_DIR"

# 2) Repo: full recursive ownership + perms + ACLs
echo "→ Setting ownership to ${SERVICE_USER}:devops (recursive)…"
chown -R "${SERVICE_USER}:devops" "$REPO"

echo "→ Enforcing directory/file modes…"
find "$REPO" -type d -exec chmod 2775 {} +
find "$REPO" -type f -exec chmod 0664 {} +

echo "→ Applying devops ACLs (now & default)…"
setfacl -R -m  g:devops:rwX "$REPO"
setfacl -R -m d:g:devops:rwX "$REPO"

echo "==> Done"
ls -ld "$REPO"
getfacl -p "$HOME_DIR" | sed -n '1,20p'
getfacl -p "$REPO"     | sed -n '1,40p'