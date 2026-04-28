#!/usr/bin/env bash
# romm-db-backup.sh
#
# Logical mariadb-dump of the RomM database, gzipped.
# Designed to run as root via systemd timer on ServiceHub LXC 103.
#
# Reads the DB password from the live decrypted .env file the deploy
# pipeline already maintains (no separate secret to manage).
#
# Rotation: keeps the last KEEP_DAYS days of backups, deletes older.
#
# Exit codes:
#   0 — backup wrote a non-empty file and rotation completed
#   1 — env file missing/unreadable, dump failed, or output was empty
set -euo pipefail

ENV_FILE="${ENV_FILE:-/home/romm/romm-hub/compose/.env}"
BACKUP_DIR="${BACKUP_DIR:-/home/romm/romm-hub/backups}"
KEEP_DAYS="${KEEP_DAYS:-30}"
CONTAINER="${CONTAINER:-romm-db}"
DATABASE="${DATABASE:-romm}"

log()   { printf '[romm-db-backup %s] %s\n' "$(date -Is)" "$*"; }
die()   { printf '[romm-db-backup %s] ERROR: %s\n' "$(date -Is)" "$*" >&2; exit 1; }

[[ -r "$ENV_FILE" ]] || die "cannot read env file: $ENV_FILE"

# Source only the password line; never the whole file (avoids accidentally
# sourcing exotic compose-substitution syntax into our shell).
PASSWORD_LINE="$(grep -E '^MYSQL_ROOT_PASSWORD=' "$ENV_FILE" || true)"
[[ -n "$PASSWORD_LINE" ]] || die "MYSQL_ROOT_PASSWORD not found in $ENV_FILE"
# shellcheck disable=SC1090
source <(printf '%s\n' "$PASSWORD_LINE")
[[ -n "${MYSQL_ROOT_PASSWORD:-}" ]] || die "MYSQL_ROOT_PASSWORD is empty"

mkdir -p "$BACKUP_DIR"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${BACKUP_DIR}/romm-${TS}.sql.gz"

log "starting backup -> ${OUT}"

# --single-transaction: consistent snapshot without locking InnoDB tables.
# --routines + --triggers: include stored procedures/triggers if any.
# --add-drop-database + --databases: backup is self-contained — restore via
#   `gzcat <file> | mariadb -uroot -p<pwd>` recreates the schema cleanly.
docker exec -i "$CONTAINER" \
  mariadb-dump \
    -uroot \
    -p"$MYSQL_ROOT_PASSWORD" \
    --single-transaction \
    --routines \
    --triggers \
    --add-drop-database \
    --databases "$DATABASE" \
  | gzip > "$OUT"

if [[ ! -s "$OUT" ]]; then
  rm -f "$OUT"
  die "dump produced empty file"
fi

SIZE_BYTES="$(stat -c '%s' "$OUT")"
log "backup OK: ${OUT} (${SIZE_BYTES} bytes)"

# Rotation
DELETED="$(find "$BACKUP_DIR" -maxdepth 1 -name 'romm-*.sql.gz' -mtime "+${KEEP_DAYS}" -print -delete | wc -l)"
log "rotated: removed ${DELETED} backup(s) older than ${KEEP_DAYS} days"

ls -la "$OUT"
