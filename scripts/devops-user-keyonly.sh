#!/usr/bin/env bash
# devops-user-keyonly.sh
# Give a user a full interactive shell but enforce KEY-ONLY SSH (no password logins).
# Idempotent, safe to re-run.
#
# Usage:
#   sudo ./devops-user-keyonly.sh <username> [shell]
# Example:
#   sudo ./devops-user-keyonly.sh firekube /bin/bash
#
# Notes:
# - Expects the user's public key to already be in ~<user>/.ssh/authorized_keys
# - Sets a per-user sshd_config.d Match block enforcing publickey-only
# - Locks the UNIX password so console/password logins fail
# - Fixes home/.ssh permissions to satisfy sshd's strict checks

set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Run as root." >&2; exit 1; }

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <username> [shell]" >&2
  exit 2
fi

USER_NAME="$1"
USER_SHELL="${2:-/bin/bash}"

# 1) Ensure user exists
if ! id "$USER_NAME" >/dev/null 2>&1; then
  echo "User '$USER_NAME' does not exist. Create with:"
  echo "  useradd -m -s $USER_SHELL -G devops $USER_NAME"
  exit 3
fi

# 2) Set shell (interactive), lock password (key-only)
chsh -s "$USER_SHELL" "$USER_NAME" || usermod -s "$USER_SHELL" "$USER_NAME"
# lock password so PAM password logins fail
usermod -L "$USER_NAME" 2>/dev/null || passwd -l "$USER_NAME" 2>/dev/null || true

# 3) Paths & permissions
USER_HOME=$(getent passwd "$USER_NAME" | cut -d: -f6)
[[ -n "${USER_HOME:-}" && -d "$USER_HOME" ]] || { echo "Home not found for $USER_NAME" >&2; exit 4; }

# create .ssh if missing
install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "$USER_HOME/.ssh"
# authorized_keys should already exist, but ensure perms/ownership are tight
if [[ -f "$USER_HOME/.ssh/authorized_keys" ]]; then
  chown "$USER_NAME":"$USER_NAME" "$USER_HOME/.ssh/authorized_keys"
  chmod 600 "$USER_HOME/.ssh/authorized_keys"
fi

# Home must not be group/other-writable; keep it readable for typical flows
chmod go-w "$USER_HOME"
# 750 is a good default (stealth to others but group-friendly). Use 755 if needed.
chmod 750 "$USER_HOME" || true

# 4) Per-user sshd config: enforce key-only for this user
SSHD_DIR="/etc/ssh/sshd_config.d"
CONF="${SSHD_DIR}/99-${USER_NAME}-keyonly.conf"
mkdir -p "$SSHD_DIR"
cat > "$CONF" <<EOF
# Auto-managed by devops-user-keyonly.sh
Match User ${USER_NAME}
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AuthenticationMethods publickey
    PubkeyAuthentication yes
    PermitTTY yes
    AllowAgentForwarding yes
    X11Forwarding no
EOF

# 5) Reload SSH daemon (no disconnect)
if systemctl reload ssh 2>/dev/null; then
  echo "Reloaded ssh"
elif systemctl reload sshd 2>/dev/null; then
  echo "Reloaded sshd"
else
  echo "WARNING: could not reload ssh/sshd; reload manually." >&2
fi

echo "== Summary =="
echo "User:        $USER_NAME"
echo "Shell:       $USER_SHELL"
echo "Home:        $USER_HOME"
echo "Key-only:    enforced via $CONF"
echo "Password:    locked (key-only)"
echo "Perms:"
ls -ld "$USER_HOME"
ls -ld "$USER_HOME/.ssh"
[[ -f "$USER_HOME/.ssh/authorized_keys" ]] && ls -l "$USER_HOME/.ssh/authorized_keys" || echo "No authorized_keys present."
