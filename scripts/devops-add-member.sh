#!/usr/bin/env bash
# devops-add-members.sh
# Add one or more users to the devops group (optionally create missing users).
# Usage:
#   ./devops-add-members.sh [-g devops] [-c] [-s /home/<svc>/<hub>] user1 [user2 ...]
#     -g  Group name (default: devops)
#     -c  Create users if missing (creates a locked account with a home dir)
#     -s  Service hub path to (re)apply group/ACLs (optional; devops-grant.sh already did this)

set -euo pipefail

GROUP="devops"
CREATE_MISSING=0
SERVICE_HUB=""

while getopts ":g:cs:" opt; do
  case "$opt" in
    g) GROUP="$OPTARG" ;;
    c) CREATE_MISSING=1 ;;
    s) SERVICE_HUB="$OPTARG" ;;
    *) echo "Usage: $0 [-g group] [-c] [-s /srv/<hub>] user1 [user2 ...]" >&2; exit 1 ;;
  esac
done
shift $((OPTIND -1))

if [[ $# -lt 1 ]]; then
  echo "No users provided."
  echo "Usage: $0 [-g group] [-c] [-s /srv/<hub>] user1 [user2 ...]" >&2
  exit 1
fi

# Ensure group exists
if ! getent group "$GROUP" >/dev/null; then
  echo "Creating group: $GROUP"
  groupadd "$GROUP"
fi

# Add users
for u in "$@"; do
  if id "$u" >/dev/null 2>&1; then
    echo "User exists: $u"
  else
    if [[ $CREATE_MISSING -eq 1 ]]; then
      echo "Creating user: $u"
      # Create with home dir, no password, login shell bash (adjust to your policy)
      useradd -m -s /bin/bash "$u"
      passwd -l "$u" >/dev/null 2>&1 || true
    else
      echo "ERROR: user '$u' does not exist. Re-run with -c to create." >&2
      continue
    fi
  fi

  # Add to group (idempotent)
  if id -nG "$u" | tr ' ' '\n' | grep -qx "$GROUP"; then
    echo "Already in $GROUP: $u"
  else
    echo "Adding $u to $GROUP"
    usermod -aG "$GROUP" "$u"
  fi
done

# (Optional) re-assert ownership/ACLs on the hub path, if given
if [[ -n "$SERVICE_HUB" ]]; then
  if [[ -d "$SERVICE_HUB" ]]; then
    echo "Re-applying group ownership and ACLs on $SERVICE_HUB"
    chgrp -R "$GROUP" "$SERVICE_HUB"
    chmod -R g+rwX "$SERVICE_HUB"
    # Default ACLs so new files/dirs inherit group rwX
    if command -v setfacl >/dev/null 2>&1; then
      setfacl -Rm g:$GROUP:rwX "$SERVICE_HUB"
      setfacl -dRm g:$GROUP:rwX "$SERVICE_HUB"
    else
      echo "setfacl not found; skipping ACLs."
    fi
  else
    echo "WARN: SERVICE_HUB '$SERVICE_HUB' not found; skipped."
  fi
fi

echo "Done. Note: users must re-login for new group membership to take effect."
