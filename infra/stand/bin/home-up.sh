#!/usr/bin/env bash
# Bring up or update the home stand using locally built images.
#   infra/stand/bin/home-up.sh <domain>
#   infra/stand/bin/home-up.sh <domain> --rollback <tag>
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../../.." && pwd)"
repo_host_root="$repo_root"
if command -v cygpath >/dev/null 2>&1; then
  repo_host_root="$(cygpath -m "$repo_root")"
fi
stand_root="${STAND_ROOT:-$HOME/flagman-stand}"
domain="${1:?usage: home-up.sh <domain> [--rollback <tag>]}"
mode="build"
rollback_tag=""

git_root="$repo_root"
if command -v cygpath >/dev/null 2>&1; then
  git_root="$(cygpath -m "$repo_root")"
fi

case "${2:-}" in
  "") ;;
  --rollback)
    rollback_tag="${3:?usage: home-up.sh <domain> --rollback <tag>}"
    [[ "$rollback_tag" =~ ^[0-9a-f]{12}$ ]] || {
      echo "error: rollback tag must be a 12-character commit tag" >&2
      exit 1
    }
    mode="rollback"
    ;;
  *)
    echo "error: expected --rollback <tag>" >&2
    exit 1
    ;;
esac

# The tag identifies the commit, so a dirty tree would make the deployed image
# different from the version that the tag appears to describe. A rollback also
# uses the checkout's Compose files and must not use an ambiguous configuration.
if [[ -n "$(git -C "$git_root" status --porcelain --untracked-files=all)" ]]; then
  echo "error: the checkout is dirty; commit or stash changes before using the stand" >&2
  exit 1
fi

export STAND_ROOT="$stand_root"
export STAND_REPO_ROOT="$repo_host_root"
"$repo_root/infra/stand/bin/init-env.sh" "$domain"

if [[ "$mode" == build ]]; then
  tag="$(git -C "$git_root" rev-parse --short=12 HEAD)"
  [[ "$tag" =~ ^[0-9a-f]{12}$ ]] || {
    echo "error: cannot derive a 12-character commit tag" >&2
    exit 1
  }
  registry=local
  echo "home-up: building local images for $tag"
  docker build --tag "$registry/flagman-api:$tag" "$repo_host_root/apps/api"
  docker build --tag "$registry/flagman-car-emulator:$tag" "$repo_host_root/services/car-emulator"
  docker build --tag "$registry/flagman-web:$tag" \
    --file "$repo_host_root/infra/stand/web/Dockerfile" \
    --build-arg "STAND_DOMAIN=$domain" "$repo_host_root"
else
  tag="$rollback_tag"
  echo "home-up: reusing local images for rollback to $tag"
fi

STAND_LOCAL_IMAGES=1 \
  STAND_REGISTRY=local \
  STAND_ROOT="$stand_root" \
  STAND_REPO_ROOT="$repo_host_root" \
  "$repo_root/infra/stand/bin/deploy.sh" "$tag" --local

echo "home-up: stand is ready; local state remains in $stand_root"
