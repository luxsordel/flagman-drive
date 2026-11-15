#!/usr/bin/env bash
# Tests for infra/stand/bin/home-up.sh against fake docker and git shims.
# Usage: bash infra/stand/tests/home-up.test.sh
set -euo pipefail

bin="$(cd "$(dirname "$0")/../bin" && pwd)"
tmp=$(mktemp -d "${TMPDIR:-/tmp}/home-up-test.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

# A minimal fake repository: has the scripts, a git repo and a stand env dir.
repo="$tmp/repo"
shims="$tmp/shims"
stand_root="$tmp/stand"
mkdir -p "$repo/infra/stand/bin" "$repo/infra/stand/web" "$shims" "$stand_root"

# On Windows/MSYS native git cannot take POSIX paths via -C; convert when cygpath is available.
repo_for_git="$repo"
if command -v cygpath >/dev/null 2>&1; then
  repo_for_git="$(cygpath -m "$repo")"
fi
cp -r "$bin/." "$repo/infra/stand/bin/"
chmod +x "$repo"/infra/stand/bin/*.sh
# Compose files must exist so deploy.sh can reference them.
touch "$repo/infra/docker-compose.yml" "$repo/infra/stand/compose.yml"

# Minimal Dockerfile for the web build arg check.
mkdir -p "$repo/infra/stand/web"
echo 'FROM scratch' >"$repo/infra/stand/web/Dockerfile"

# A real git repo with all scripts committed so home-up.sh's dirty-checkout guard works.
git -C "$repo_for_git" init -q
git -C "$repo_for_git" config user.email test@example.test
git -C "$repo_for_git" config user.name "Test"
git -C "$repo_for_git" add -A
git -C "$repo_for_git" commit -q -m "initial"

# Pre-populate stand.env so init-env.sh does not need to generate secrets.
cat >"$stand_root/stand.env" <<'EOF'
STAND_DOMAIN=example.test
STAND_REGISTRY=local
ACME_EMAIL=test@example.test
API_WORKERS=1
API_KEEP_ALIVE_SECONDS=75
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=5
POSTGRES_SUPERUSER_PASSWORD=aabbccdd00112233aabbccdd
POSTGRES_OWNER_PASSWORD=aabbccdd00112233aabbccdd
POSTGRES_PASSWORD=aabbccdd00112233aabbccdd
REDIS_PASSWORD=aabbccdd00112233aabbccdd
API_JWT_SECRET=aabbccdd00112233aabbccdd00112233aabbccdd00112233aabbccdd00112233
API_OTP_PEPPER=aabbccdd00112233aabbccdd00112233aabbccdd00112233aabbccdd00112233
API_TOTP_ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
SMS_FIXED_CODE=472918
SMS_FIXED_PHONES=+375291111112,+375291112222
S3_ACCESS_KEY=aabbccdd00112233
S3_SECRET_KEY=aabbccdd00112233aabbccdd00112233aabbccdd00112233aabbccdd00112233
PAYMENTS_WEBHOOK_SECRET=aabbccdd00112233aabbccdd00112233aabbccdd00112233aabbccdd00112233
PAYMENTS_TOKEN_ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
EMULATOR_COMMAND_SECRET=aabbccdd00112233aabbccdd00112233aabbccdd00112233aabbccdd00112233
EMULATOR_TELEMETRY_SECRET=aabbccdd00112233aabbccdd00112233aabbccdd00112233aabbccdd00112233
STAND_ADMIN_EMAIL=admin@stand.local
STAND_ADMIN_PASSWORD=aabbccdd00112233aabbccdd00112233aabbccdd
STAND_ADMIN_TOTP_SECRET=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PX
STAND_OPERATOR_EMAIL=operator@stand.local
STAND_OPERATOR_PASSWORD=aabbccdd00112233aabbccdd00112233aabbccdd
STAND_OPERATOR_TOTP_SECRET=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PX
STAND_TECHNICIAN_EMAIL=technician@stand.local
STAND_TECHNICIAN_PASSWORD=aabbccdd00112233aabbccdd00112233aabbccdd
STAND_TECHNICIAN_TOTP_SECRET=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PX
MAP_ORIGINS=https://tiles.openfreemap.org
EOF

# Fake docker: records every call, returns success; docker build outputs a fake image ID.
cat >"$shims/docker" <<'EOF'
#!/usr/bin/env bash
echo "$*" >>"$(dirname "$0")/calls"
args=" $* "
case "$args" in
  *" build "*) echo "sha256:aaaa" ;;
  *" run "*) ;;
  *" compose "*) ;;
  *) ;;
esac
exit 0
EOF
chmod +x "$shims/docker"

# Fake git: delegates to real git for status/rev-parse; intercepts nothing.
# (We use the real git since the repo is a real git repo.)

failed=0
out=""
code=0

run_home_up() {
  code=0
  out=$(STAND_ROOT="$stand_root" PATH="$shims:$PATH" \
    bash "$repo/infra/stand/bin/home-up.sh" "$@" 2>&1) || code=$?
}

expect() {
  local name=$1; shift
  if "$@"; then
    echo "ok   $name"
  else
    echo "FAIL $name"
    printf '     %s\n' "$out"
    failed=1
  fi
}
calls() { grep -cF -- "$1" "$shims/calls" 2>/dev/null || true; }

# --- dirty checkout stops the script ---
echo "dirty-file" >"$repo/untracked.txt"
run_home_up example.test
expect "dirty checkout stops home-up" test "$code" -ne 0
expect "dirty checkout prints an error" grep -q "dirty" <<<"$out"
rm "$repo/untracked.txt"
rm -f "$shims/calls"

# --- build mode: three images are built, deploy.sh called with --local ---
run_home_up example.test
expect "build mode succeeds" test "$code" -eq 0
expect "build mode builds the api image"          test "$(calls "build --tag local/flagman-api:")" -eq 1
expect "build mode builds the emulator image"     test "$(calls "build --tag local/flagman-car-emulator:")" -eq 1
expect "build mode builds the web image"          test "$(calls "build --tag local/flagman-web:")" -eq 1
tag="$(git -C "$repo_for_git" rev-parse --short=12 HEAD)"
expect "build mode passes the commit tag to deploy" grep -qF "$tag --local" "$shims/calls"
rm -f "$shims/calls"

# --- rollback: bad tag rejected before any build ---
run_home_up example.test --rollback badhex
expect "rollback with a bad tag exits non-zero" test "$code" -ne 0
expect "rollback with a bad tag does not build" test "$(calls "build")" -eq 0
rm -f "$shims/calls"

# --- rollback: good tag reuses images, no build ---
run_home_up example.test --rollback "$(printf '%012x' 1)"
expect "rollback with a good tag succeeds" test "$code" -eq 0
expect "rollback does not build any image" test "$(calls "build")" -eq 0
rm -f "$shims/calls"

exit "$failed"
