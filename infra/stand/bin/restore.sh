#!/usr/bin/env bash
# Restores the stand database from a daily dump.
#   infra/stand/bin/restore.sh                     lists the dumps
#   infra/stand/bin/restore.sh flagman-<time>.dump replaces the database with that dump
# The current state is dumped first, so a wrong pick can be undone the same way.
set -euo pipefail
# shellcheck source=infra/stand/bin/common.sh
source "$(dirname "$0")/common.sh"

STAND_TAG="$(last_deployed_tag)"
if [[ -z "$STAND_TAG" ]]; then
  echo "error: nothing is deployed yet ($STAND_TAGS_LOG is empty)" >&2
  exit 1
fi
export STAND_TAG

if [[ $# -eq 0 ]]; then
  stand_compose exec -T backup ls -lh /backups
  exit 0
fi

file="$1"
# A bare name from the list: nothing outside the backups volume can be passed in.
if [[ ! "$file" =~ ^flagman-[0-9TZ]+\.dump$ ]]; then
  echo "error: expected a name like flagman-20261028T030000Z.dump from the list" >&2
  exit 1
fi
stand_compose exec -T backup test -f "/backups/$file"
db="$(stand_env POSTGRES_DB)"

echo "restore: dumping the current state first"
stand_compose exec -T backup sh /stand/bin/backup.sh

echo "restore: stopping everything that writes to $db"
stand_compose stop web api worker car-emulator
stand_compose exec -T backup psql --dbname=postgres -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE \"$db\" WITH (FORCE)" -c "CREATE DATABASE \"$db\""
stand_compose exec -T backup pg_restore --no-owner --exit-on-error --dbname="$db" "/backups/$file"

# The dump may be older than the deployed image's schema.
stand_compose run --rm -T api alembic upgrade head
stand_compose up -d
echo "restore: $file restored, the stand is starting"
