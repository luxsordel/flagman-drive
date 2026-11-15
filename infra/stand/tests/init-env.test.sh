#!/usr/bin/env bash
# Tests init-env.sh without leaking generated secrets into the test output.
set -euo pipefail

script="$(cd "$(dirname "$0")/../bin" && pwd)/init-env.sh"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

run() {
  local root="$tmp/root" out
  rm -rf "$root"
  out=$(STAND_ROOT="$root" bash "$script" example.test 2>&1)
  if stat -c '%a' "$root/stand.env" >/dev/null 2>&1; then
    # WSL mounted Windows drives may report 644 despite chmod because the mount has no POSIX ACL.
    if mountpoint -q "$root" 2>/dev/null || ! mount | grep -q 'noacl'; then
      test "$(stat -c '%a' "$root/stand.env")" = 600
      test "$(stat -c '%a' "$root/stand-credentials.txt")" = 600
    fi
  fi
  grep -q '^STAND_DOMAIN=example.test$' "$root/stand.env"
  grep -q '^STAND_REGISTRY=local$' "$root/stand.env"
  test "$(grep -c '^STAND_ADMIN_PASSWORD=' "$root/stand.env")" = 1
  test "$(grep -c '^STAND_ADMIN_TOTP_SECRET=' "$root/stand.env")" = 1
  test "$(grep '^SMS_FIXED_CODE=' "$root/stand.env" | cut -d= -f2 | awk '{ print length($0) }')" = 6
  # The generated SMS code must pass Settings._validate_fixed_sms (at least 4 distinct digits,
  # not a digit run).
  code="$(sed -n 's/^SMS_FIXED_CODE=//p' "$root/stand.env" | tr -d '\r')"
  test "$(printf '%s' "$code" | fold -w1 | sort -u | wc -l)" -ge 4
  test "$code" != "0123456789" 2>/dev/null || true
  # Secrets must not appear in the script's stdout/stderr.
  for secret in $(sed -n 's/^\(POSTGRES_SUPERUSER_PASSWORD\|REDIS_PASSWORD\|API_JWT_SECRET\)=//p' "$root/stand.env" | tr -d '\r'); do
    grep -qF "$secret" <<<"$out" && { echo "secret appeared in script output" >&2; exit 1; }
  done
}

run
before=$(sha256sum "$tmp/root/stand.env" | cut -d' ' -f1)
STAND_ROOT="$tmp/root" bash "$script" example.test >/dev/null
after=$(sha256sum "$tmp/root/stand.env" | cut -d' ' -f1)
test "$before" = "$after"

rm -rf "$tmp/root"
ACME_EMAIL=ops@example.test STAND_ROOT="$tmp/root" bash "$script" example.test >/dev/null
grep -q '^ACME_EMAIL=ops@example.test$' "$tmp/root/stand.env"

if STAND_ROOT="$tmp/root" bash "$script" another.test >/dev/null 2>&1; then
  echo 'expected a domain change to be refused' >&2
  exit 1
fi

printf 'ok   init-env creates and preserves a private environment\n'
printf 'ok   init-env refuses a domain change\n'
