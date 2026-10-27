#!/bin/sh
# Dumps the stand database into BACKUP_DIR and drops dumps older than BACKUP_KEEP_DAYS days.
# Runs inside the backup service (infra/stand/compose.yml), where PG* point at the database:
#   backup.sh          one dump now:  docker compose ... exec backup sh /stand/bin/backup.sh
#   backup.sh --loop   one dump a day, the service's entrypoint
# POSIX sh: the postgis image has no bash.
set -eu

dir="${BACKUP_DIR:-/backups}"
keep_days="${BACKUP_KEEP_DAYS:-7}"

# Every step checks its own exit code: in --loop dump runs as an if condition, where set -e
# does nothing, and a failed pg_dump must neither pass for a good dump nor cost the old ones.
dump() {
  name="flagman-$(date -u +%Y%m%dT%H%M%SZ).dump"
  # Written under another name first: a dump cut short must never look like a good one.
  if ! pg_dump --format=custom --no-owner --file="$dir/.partial-$name"; then
    rm -f "$dir/.partial-$name"
    return 1
  fi
  mv "$dir/.partial-$name" "$dir/$name" || return 1
  # -mtime +N matches files older than N+1 whole days.
  find "$dir" -maxdepth 1 -name 'flagman-*.dump' -mtime +"$((keep_days - 1))" -delete || return 1
  find "$dir" -maxdepth 1 -name '.partial-*' -mtime +0 -delete || return 1
  echo "backup: $name done, kept for $keep_days days"
}

if [ "${1:-}" != "--loop" ]; then
  dump
  exit 0
fi

while true; do
  if dump; then
    sleep 86400
  else
    echo "backup: failed, next try in an hour" >&2
    sleep 3600
  fi
done
