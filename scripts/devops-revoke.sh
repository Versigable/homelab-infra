#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <repo_path>"
  echo "Example: $0 /home/wiki/wiki-hub"
  exit 1
fi

REPO="$1"
[[ -d "$REPO" ]] || { echo "Repo not found: $REPO" >&2; exit 2; }

# Remove devops ACLs and restore owner-only group perms (keep group as devops if desired)
setfacl -Rb "$REPO"
find "$REPO" -type d -exec chmod 0750 {} +
find "$REPO" -type f -exec chmod 0640 {} +

echo "==> Revoke complete (collaboration disabled)"
ls -ld "$REPO"
