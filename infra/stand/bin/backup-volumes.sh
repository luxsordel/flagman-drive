#!/usr/bin/env bash
# Archive the stand's Docker volumes that are not covered by pg_dump.
#   infra/stand/bin/backup-volumes.sh
set -euo pipefail
# shellcheck source=infra/stand/bin/common.sh
source "$(dirname "$0")/common.sh"

backup_dir="${STAND_VOLUME_BACKUP_DIR:-$STAND_ROOT/volume-backups}"
project="${STAND_COMPOSE_PROJECT:-flagman}"
helper_image="${STAND_VOLUME_BACKUP_IMAGE:-caddy:2.10.2-alpine}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"

archive_volume() {
  local volume="$1" archive tmp
  volume="${project}_${volume}"
  docker volume inspect "$volume" >/dev/null
  archive="$backup_dir/${volume}-${timestamp}.tar.gz"
  tmp="$backup_dir/.partial-$(basename "$archive")"
  docker run --rm --user 0:0 \
    -v "$volume:/source:ro" \
    -v "$backup_dir:/backup" \
    "$helper_image" \
    tar -czf "/backup/$(basename "$tmp")" -C /source .
  mv "$tmp" "$archive"
  chmod 600 "$archive"
  echo "backup-volumes: $volume archived"
}

# pgdata is deliberately excluded: a raw PostgreSQL data directory is not a consistent backup.
# The backup service's pg_dump is the database backup; pgbackups is archived so those dumps survive
# loss of the Docker volume. Caddy state is archived because it is not recreated by the application.
archive_volume caddydata
archive_volume caddyconfig
archive_volume pgbackups
