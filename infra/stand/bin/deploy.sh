#!/usr/bin/env bash
# Deploys the stand to the images tagged <tag>, or rolls it back to an earlier tag the same way.
#   infra/stand/bin/deploy.sh <tag>
# Local mode is used by home-up.sh: images were built in this checkout and must not be pulled.
set -euo pipefail
# shellcheck source=infra/stand/bin/common.sh
source "$(dirname "$0")/common.sh"

new_tag="${1:?usage: deploy.sh <tag> [--local]}"
local_mode="${2:-}"
if [[ "$local_mode" != "" && "$local_mode" != "--local" ]]; then
  echo "error: expected --local as the second argument" >&2
  exit 1
fi
old_tag="$(last_deployed_tag)"
registry="$(stand_env STAND_REGISTRY)"
api_image="$registry/flagman-api"
# Tags whose images stay on the server for a rollback without a pull; older ones are removed.
keep_tags=5

alembic_in() {
  docker run --rm --entrypoint alembic "$api_image:$1" "${@:2}"
}

# Alembic prints revisions among the API's JSON log lines; a revision line starts with its hex id.
revision_ids() {
  grep -oE '^[0-9a-f]{6,}\b' || true
}

# Images of the stand whose tags are not among the last keep_tags deploys. One still in use by a
# container is refused by docker and stays. Local images are retained: the home stand must be able
# to roll back to any tag recorded in its deployment log.
remove_old_images() {
  local kept image
  if [[ "$local_mode" == "--local" ]]; then
    return 0
  fi
  kept="$(tail -n "$keep_tags" "$STAND_TAGS_LOG" | cut -d ' ' -f 2)"
  docker image ls --format '{{.Repository}}:{{.Tag}}' | { grep "^$registry/flagman-" || true; } \
    | while read -r image; do
      if ! grep -qxF "${image##*:}" <<<"$kept"; then
        docker image rm "$image" >/dev/null 2>&1 || true
      fi
    done
  docker image prune --force >/dev/null
}

echo "deploy: $old_tag -> $new_tag"
if [[ "$local_mode" == "--local" ]]; then
  echo "deploy: using locally built images"
else
  STAND_TAG="$new_tag" stand_compose pull --quiet
fi
# Before anything changes: a broken stand.env must stop the deploy while the old version runs.
"$(dirname "$0")/seed.sh" "$new_tag" --check

# The roles of docs/adr/0031 before anything connects as them: on the first deploy with them
# they do not exist yet, and a changed password of the owner or the app reaches the database here
# (the superuser's is set only when the volume is created).
STAND_TAG="$new_tag" stand_compose up -d --wait postgres
STAND_TAG="$new_tag" stand_compose exec -T postgres flagman-bootstrap-roles

# A rollback to an image with fewer migrations: the newer image is the only one that knows how
# to undo them, so it takes the schema down to the old image's head before the switch.
# Read with the deployed image: an older one cannot name a revision it does not have. An image
# from before the migrate service (#167) fails here and reads as no revision: it is never newer.
# Its stderr stays hidden: a settings ValidationError can print the values of the environment,
# passwords included, into the workflow log.
if ! current="$(STAND_TAG="${old_tag:-$new_tag}" stand_compose run --rm -T migrate alembic current \
  2>/dev/null)"; then
  echo "deploy: warning: cannot read the schema revision with ${old_tag:-$new_tag}; taken as none." \
    "Why: on the server, docker compose ... run --rm migrate alembic current" >&2
  current=""
fi
db_revision="$(revision_ids <<<"$current" | tail -n 1)"
if [[ -n "$db_revision" ]] && ! alembic_in "$new_tag" show "$db_revision" >/dev/null 2>&1; then
  if [[ -z "$old_tag" ]]; then
    echo "error: the schema is at $db_revision, unknown to $new_tag, and there is no previous tag" >&2
    exit 1
  fi
  target="$(alembic_in "$new_tag" heads | revision_ids)"
  echo "deploy: schema $db_revision is newer than $new_tag, downgrading to $target with $old_tag"
  STAND_TAG="$old_tag" stand_compose run --rm -T migrate alembic downgrade "$target"
fi

# Before the new API starts: it expects the schema it was built with.
STAND_TAG="$new_tag" stand_compose run --rm -T migrate alembic upgrade head
"$(dirname "$0")/seed.sh" "$new_tag"
STAND_TAG="$new_tag" stand_compose up -d --remove-orphans

echo "deploy: waiting for the API"
for _ in $(seq 1 60); do
  if STAND_TAG="$new_tag" stand_compose exec -T api python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=2)" \
    >/dev/null 2>&1; then
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$new_tag" >>"$STAND_TAGS_LOG"
    remove_old_images
    if [[ -n "$old_tag" && "$old_tag" != "$new_tag" ]]; then
      echo "deploy: $new_tag is up; roll back with deploy.sh $old_tag"
    else
      echo "deploy: $new_tag is up"
    fi
    exit 0
  fi
  sleep 2
done

echo "error: the API is not ready after 2 minutes; logs: docker compose ... logs api" >&2
STAND_TAG="$new_tag" stand_compose ps >&2
exit 1
