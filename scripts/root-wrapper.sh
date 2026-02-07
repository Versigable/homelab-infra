cat >/usr/local/sbin/gitops-sync <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

SRC="${1:-}"
DST="${2:-}"

usage() { echo "usage: gitops-sync <src_dir> <dst_dir>" >&2; exit 2; }

[[ -n "$SRC" && -n "$DST" ]] || usage
[[ -d "$SRC" ]] || { echo "missing src dir: $SRC" >&2; exit 2; }
[[ -d "$DST" ]] || { echo "missing dst dir: $DST" >&2; exit 2; }

# Safety rails: only allow syncing OUT of a gitlab-runner build dir, INTO /home/*/*-hub/*
case "$SRC" in
  /home/gitlab-runner/builds/*) ;;
  *) echo "refusing SRC (not in runner builds): $SRC" >&2; exit 3 ;;
esac

case "$DST" in
  /home/*/*-hub/*) ;;
  *) echo "refusing DST (not under /home/*/*-hub/*): $DST" >&2; exit 4 ;;
esac

exec /usr/bin/rsync -a --delete "$SRC"/ "$DST"/
EOF

chmod 0755 /usr/local/sbin/gitops-sync
chown root:root /usr/local/sbin/gitops-sync

# quick check
/usr/local/sbin/gitops-sync /tmp /tmp >/dev/null 2>&1 || true
echo "gitops-sync installed: $(ls -l /usr/local/sbin/gitops-sync)"
