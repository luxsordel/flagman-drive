#!/usr/bin/env bash
# Seeds the stand: zones, fleet, tariffs, staff with 2FA. Safe to run again, deploy.sh runs it
# on every deploy so new seed data reaches the stand.
#   infra/stand/bin/seed.sh <tag>           seed
#   infra/stand/bin/seed.sh <tag> --check   only check the staff passwords and 2FA secrets
set -euo pipefail
# shellcheck source=infra/stand/bin/common.sh
source "$(dirname "$0")/common.sh"

export STAND_TAG="${1:?usage: seed.sh <tag> [--check]}"
check=()
if [[ "${2:-}" == "--check" ]]; then
  check=(--check)
else
  # In this order: tariffs go to the car models the fleet seed creates (apps/api/README.md).
  for command in flagman-geo flagman-fleet flagman-pricing; do
    stand_compose run --rm -T api "$command" seed
  done
fi

for role in admin operator technician; do
  prefix="STAND_${role^^}"
  email="$(stand_env "${prefix}_EMAIL")"
  if [[ -z "$email" ]]; then
    echo "seed: ${prefix}_EMAIL is empty, no $role on the stand"
    continue
  fi
  # Through stdin: never in the arguments, which end up in the process list.
  printf '%s\n%s\n' "$(stand_env "${prefix}_PASSWORD")" "$(stand_env "${prefix}_TOTP_SECRET")" \
    | stand_compose run --rm -T api flagman-staff ensure --email "$email" --role "$role" "${check[@]}"
done
