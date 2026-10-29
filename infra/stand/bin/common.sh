#!/usr/bin/env bash
# Shared by the stand scripts: where the stand lives and how to call compose there.
# Sourced, not run.

# /opt/flagman on the server: infra/ is copied there by the deploy workflow, stand.env is made by
# hand once (docs/08-demo-stand.md) and never leaves the server.
STAND_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
STAND_ENV="$STAND_ROOT/stand.env"
# One line per deploy: "<UTC time> <tag>". The line before the last is what a rollback goes to.
STAND_TAGS_LOG="$STAND_ROOT/deployed-tags.log"

if [[ ! -f "$STAND_ENV" ]]; then
  echo "error: $STAND_ENV is missing, see docs/08-demo-stand.md" >&2
  exit 1
fi

# One value from stand.env. Read rather than sourced: the file is in compose's format, and a
# secret with a $ or a quote in it would break or run as shell.
stand_env() {
  sed -n "s/^$1=//p" "$STAND_ENV" | tail -n 1
}

# compose for the images of STAND_TAG (set by the caller).
stand_compose() {
  STAND_TAG="${STAND_TAG:?STAND_TAG is not set}" docker compose \
    --env-file "$STAND_ENV" \
    -f "$STAND_ROOT/infra/docker-compose.yml" \
    -f "$STAND_ROOT/infra/stand/compose.yml" \
    "$@"
}

last_deployed_tag() {
  if [[ -f "$STAND_TAGS_LOG" ]]; then
    tail -n 1 "$STAND_TAGS_LOG" | cut -d ' ' -f 2
  fi
}
