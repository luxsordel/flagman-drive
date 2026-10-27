#!/usr/bin/env bash
# Tests for infra/stand/bin/deploy.sh against a fake docker, in a temporary copy of the stand.
# Usage: bash infra/stand/tests/deploy.test.sh
# The fake reads its answers from files next to it: db-revision is what `alembic current` prints,
# revs-<tag> are the migrations of an image, the last one its head, images is `docker image ls`,
# bad-env makes `flagman-staff ensure --check` fail. Every call goes to calls as "<STAND_TAG> <args>".
set -euo pipefail

bin="$(cd "$(dirname "$0")/../bin" && pwd)"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
root="$tmp/opt"
shims="$tmp/shims"
mkdir -p "$root/infra/stand" "$shims"
cp -r "$bin" "$root/infra/stand/bin"
cat >"$root/stand.env" <<'EOF'
STAND_REGISTRY=ghcr.io/flagman
STAND_OPERATOR_EMAIL=operator@stand.test
STAND_OPERATOR_PASSWORD=correct horse battery staple
STAND_OPERATOR_TOTP_SECRET=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP
EOF

cat >"$shims/docker" <<'EOF'
#!/usr/bin/env bash
d=$(dirname "$0")
echo "${STAND_TAG:--} $*" >>"$d/calls"
args=" $* "
case "$args" in
  *" ensure "*)
    cat >/dev/null
    [[ "$args" == *" --check "* && -f "$d/bad-env" ]] && { echo "error: weak password" >&2; exit 1; }
    exit 0 ;;
  *" alembic current "*) [ -s "$d/db-revision" ] && echo "$(cat "$d/db-revision") (head)"; exit 0 ;;
  *" --entrypoint alembic "*)
    revs="$d/revs-${5##*:}"
    case "$6" in
      show) grep -qxF "$7" "$revs" ;;
      heads) echo "$(tail -n 1 "$revs") (head)" ;;
    esac ;;
  " image ls "*) cat "$d/images" ;;
  *) exit 0 ;;
esac
EOF
chmod +x "$shims/docker"

failed=0
code=0
out=""

# scenario <db revision> <deployed tags...>: a fresh fake with the stand at that state.
scenario() {
  rm -f "$shims/calls" "$shims/bad-env" "$root/deployed-tags.log"
  echo "$1" >"$shims/db-revision"
  shift
  : >"$shims/images"
  local tag
  for tag; do
    echo "2026-10-01T00:00:00Z $tag" >>"$root/deployed-tags.log"
  done
}

deploy() {
  code=0
  out=$(PATH="$shims:$PATH" bash "$root/infra/stand/bin/deploy.sh" "$@" 2>&1) || code=$?
}

expect() {
  local name=$1
  shift
  if "$@"; then
    echo "ok   $name"
  else
    echo "FAIL $name"
    { echo "$out"; echo "-- calls:"; cat "$shims/calls"; } | sed 's/^/     /'
    failed=1
  fi
}

# How many calls contain the text; the first line with it.
calls() { grep -cF -- "$1" "$shims/calls" || true; }
line_of() { grep -nF -- "$1" "$shims/calls" | head -n 1 | cut -d: -f1; }
last_tag() { tail -n 1 "$root/deployed-tags.log" | cut -d ' ' -f 2; }

compose="compose --env-file $root/stand.env -f $root/infra/docker-compose.yml -f $root/infra/stand/compose.yml"
printf '%s
' aaaa01 >"$shims/revs-old"
printf '%s
' aaaa01 bbbb02 >"$shims/revs-new"

scenario ""
deploy new
expect "the first deploy succeeds" test "$code" -eq 0
expect "the first deploy does not downgrade" test "$(calls "alembic downgrade")" -eq 0
expect "the first deploy migrates with the new image"   test "$(calls "new $compose run --rm -T api alembic upgrade head")" -eq 1
expect "the first deploy is logged" test "$(last_tag)" = new

scenario aaaa01 old
deploy new
expect "an upgrade succeeds" test "$code" -eq 0
expect "an upgrade does not downgrade" test "$(calls "alembic downgrade")" -eq 0

scenario bbbb02 old new
deploy old
expect "a rollback succeeds" test "$code" -eq 0
expect "a rollback downgrades with the newer image to the older head"   test "$(calls "new $compose run --rm -T api alembic downgrade aaaa01")" -eq 1
expect "a rollback downgrades before it migrates"   test "$(line_of "alembic downgrade")" -lt "$(line_of "old $compose run --rm -T api alembic upgrade head")"

scenario bbbb02
deploy old
expect "a schema unknown to the image with no previous tag stops the deploy" test "$code" -ne 0
expect "a schema unknown to the image is not migrated" test "$(calls "alembic upgrade")" -eq 0
expect "a schema unknown to the image is not downgraded" test "$(calls "alembic downgrade")" -eq 0

scenario aaaa01 old
touch "$shims/bad-env"
deploy new
expect "a broken stand.env stops the deploy" test "$code" -ne 0
expect "a broken stand.env stops it before the migrations" test "$(calls "alembic upgrade")" -eq 0
expect "a broken stand.env does not restart anything" test "$(calls " up -d")" -eq 0
expect "a broken stand.env is not logged as a deploy" test "$(last_tag)" = old

scenario aaaa01 t1 t2 t3 t4 t5
for tag in t1 t2 t3 t4 t5 t6; do
  echo "ghcr.io/flagman/flagman-api:$tag" >>"$shims/images"
  echo "ghcr.io/flagman/flagman-web:$tag" >>"$shims/images"
done
echo "postgis/postgis:16-3.6" >>"$shims/images"
printf '%s
' aaaa01 >"$shims/revs-t6"
deploy t6
expect "images of an older deploy go" test "$(calls "image rm ghcr.io/flagman/flagman-")" -eq 2
expect "the API image of the older deploy goes" test "$(calls "image rm ghcr.io/flagman/flagman-api:t1")" -eq 1
expect "the web image of the older deploy goes" test "$(calls "image rm ghcr.io/flagman/flagman-web:t1")" -eq 1
expect "images of other projects stay" test "$(calls "image rm postgis")" -eq 0

exit "$failed"
