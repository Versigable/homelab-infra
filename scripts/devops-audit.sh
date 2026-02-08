#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <repo_path>"
  echo "Example: $0 /home/authentik/authentik-hub"
  exit 1
fi

REPO="$1"
[[ -d "$REPO" ]] || { echo "Repo not found: $REPO" >&2; exit 2; }

echo "== Repo summary =="
namei -l "$REPO" || true
echo

echo "== Repo ACL (top) =="
getfacl -p "$REPO" | sed -n '1,40p' || true
echo

echo "== Sample file modes =="
find "$REPO" -maxdepth 2 -type f -printf '%M %u %g %p\n' | head -n 15 || true
