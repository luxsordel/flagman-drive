#!/bin/sh
# Dumps the stand database into /backups and drops dumps older than BACKUP_KEEP_DAYS days.
# Runs inside the backup service (infra/stand/compose.yml), where PG* point at the database:
#   backup.sh          one dump now:  docker compose ... exec backup sh /stand/bin/backup.sh
#   backup.sh --loop   one dump a day, the service's entrypoint
# POSIX sh: the postgis image has no bash.
set -eu

dir=/backups
keep_days="${BACKUP_KEEP_DAYS:-7}"

dump() {
  name="flagman-$(date -u +%Y%m%dT%H%M%SZ).dump"
  # Written under another name first: a dump cut short must never look like a good one.
  pg_dump --format=custom --no-owner --file="$dir/.partial-$name"
  mv "$dir/.partial-$name" "$dir/$name"
  # -mtime +N matches files older than N+1 whole days.
  find "$dir" -maxdepth 1 -name 'flagman-*.dump' -mtime +"$((keep_days - 1))" -delete
  find "$dir" -maxdepth 1 -name '.partial-*' -mtime +0 -delete
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
