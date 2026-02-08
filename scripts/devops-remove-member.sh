#!/usr/bin/env bash
# devops-remove-member.sh
# Remove one or more users from a DevOps-style collaboration group.
# Optionally lock or delete the user accounts after removal.
#
# Usage:
#   sudo ./devops-remove-member.sh [-g devops] [--lock] [--delete-user] user1 [user2 ...]
#
# Options:
#   -g <group>       Target group name (default: devops)
#   --lock           Lock the user account after removal (passwd -l)
#   --delete-user    Delete the user account and home after removal (userdel -r)
#
# Notes:
#  - Idempotent: safe to re-run; skips non-members automatically.
#  - Uses gpasswd -d when available; falls back to deluser/usermod where needed.
#  - Does NOT alter hub ACLs/ownership; other devops members keep access.
#  - Pair with devops-audit.sh if you want to verify final permissions.

set -euo pipefail

GROUP="devops"
LOCK_AFTER=0
DELETE_USER=0

usage() {
  cat >&2 <<EOF
Usage: $0 [-g group] [--lock] [--delete-user] user1 [user2 ...]
  -g <group>       Target group (default: devops)
  --lock           Lock account(s) after removal
  --delete-user    Delete account(s) after removal (userdel -r)
EOF
  exit 1
}

# Parse short options
while getopts ":g:-:" opt; do
  case "$opt" in
    g) GROUP="$OPTARG" ;;
    -)
      case "${OPTARG}" in
        lock) LOCK_AFTER=1 ;;
        delete-user) DELETE_USER=1 ;;
        *) usage ;;
      esac
      ;;
    \?) usage ;;
    :) usage ;;
  esac
done
shift $((OPTIND - 1))

[[ $# -ge 1 ]] || usage

# Ensure target group exists (we can still proceed if not, but warn)
if ! getent group "$GROUP" >/dev/null; then
  echo "WARN: group '$GROUP' does not exist; proceeding (users may already be non-members)." >&2
fi

remove_from_group() {
  local user="$1" group="$2"

  # If group doesn't exist, nothing to remove.
  if ! getent group "$group" >/dev/null; then
    echo "  - Skipping group removal (group '$group' missing)."
    return 0
  fi

  # Check membership
  if id -nG "$user" 2>/dev/null | tr ' ' '\n' | grep -qx "$group"; then
    echo "  - Removing '$user' from '$group'…"
    if command -v gpasswd >/dev/null 2>&1; then
      gpasswd -d "$user" "$group" >/dev/null
    elif command -v deluser >/dev/null 2>&1; then
      deluser "$user" "$group" >/dev/null
    else
      # Fallback: rewrite supplementary group list without $group
      current="$(id -nG "$user" | tr ' ' ',')"
      new="$(id -nG "$user" | tr ' ' '\n' | grep -vx "$group" | paste -sd, -)"
      usermod -G "$new" "$user"
    fi
  else
    echo "  - '$user' is not a member of '$group' (ok)."
  fi
}

for u in "$@"; do
  echo "User: $u"
  if ! id "$u" >/dev/null 2>&1; then
    echo "  - User does not exist; nothing to do."
    continue
  fi

  # Guard: if target group is the user's *primary* group, we should not try to remove it as a supplementary group.
  primary_group="$(id -gn "$u")"
  if [[ "$primary_group" == "$GROUP" ]]; then
    echo "  - NOTE: '$GROUP' is the primary group for '$u'. Skipping group removal (change primary group first if truly needed)."
  else
    remove_from_group "$u" "$GROUP"
  fi

  if [[ $LOCK_AFTER -eq 1 ]]; then
    echo "  - Locking account '$u'…"
    passwd -l "$u" >/dev/null
  fi

  if [[ $DELETE_USER -eq 1 ]]; then
    echo "  - Deleting account '$u' (home & mail spool) …"
    # Safety: refuse to delete if user is still in the target group (e.g., primary-group case).
    if id -nG "$u" 2>/dev/null | tr ' ' '\n' | grep -qx "$GROUP"; then
      echo "    ! Refusing to delete '$u' while still in '$GROUP' (primary group?). Adjust primary group then re-run." >&2
    else
      userdel -r "$u"
    fi
  fi

  echo "  - Done with '$u'."
done

echo "All set. Users should re-log to reflect group changes."
