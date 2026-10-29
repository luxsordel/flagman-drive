#!/usr/bin/env bash
# Tests for infra/stand/bin/backup.sh against fake pg_dump and sleep, in a temporary directory.
# Usage: bash infra/stand/tests/backup.test.sh
set -euo pipefail

script="$(cd "$(dirname "$0")/../bin" && pwd)/backup.sh"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
shims="$tmp/shims"
dir="$tmp/backups"
mkdir "$shims"

# Writes part of a dump and fails when the file "fail" exists, like a dump cut short.
cat >"$shims/pg_dump" <<'EOF'
#!/usr/bin/env bash
d=$(dirname "$0")
for arg; do [[ "$arg" == --file=* ]] && file=${arg#--file=}; done
echo partial >"$file"
[ -f "$d/fail" ] && { echo "pg_dump: connection refused" >&2; exit 1; }
echo dump >"$file"
EOF
# Records how long the loop would wait and stops it, so --loop runs one round.
cat >"$shims/sleep" <<'EOF'
#!/usr/bin/env bash
echo "$1" >"$(dirname "$0")/slept"
exit 99
EOF
chmod +x "$shims/pg_dump" "$shims/sleep"

failed=0
code=0
out=""

run() {
  rm -rf "$dir" "$shims/slept"
  mkdir "$dir"
  touch -d "@$(($(date +%s) - 30 * 86400))" "$dir/flagman-old.dump"
  code=0
  out=$(PATH="$shims:$PATH" BACKUP_DIR="$dir" BACKUP_KEEP_DAYS=7 sh "$script" "$@" 2>&1) || code=$?
}

expect() {
  local name=$1
  shift
  if "$@"; then
    echo "ok   $name"
  else
    echo "FAIL $name"
    while IFS= read -r line; do echo "     $line"; done <<<"$out"
    failed=1
  fi
}

dumps() { find "$dir" -maxdepth 1 -name 'flagman-2*.dump' | wc -l; }
partials() { find "$dir" -maxdepth 1 -name '.partial-*' | wc -l; }

rm -f "$shims/fail"
run
expect "a dump is made under its final name" test "$code" -eq 0 -a "$(dumps)" -eq 1 -a "$(partials)" -eq 0
expect "a dump older than the kept days is removed" test ! -e "$dir/flagman-old.dump"

touch "$shims/fail"
run
expect "a failed dump fails the run" test "$code" -ne 0
expect "a failed dump leaves no dump and no partial file" test "$(dumps)" -eq 0 -a "$(partials)" -eq 0
expect "a failed dump keeps the old dumps" test -e "$dir/flagman-old.dump"

run --loop
expect "the loop retries a failed dump in an hour" test "$(cat "$shims/slept")" = 3600
expect "the loop leaves no dump after a failure" test "$(dumps)" -eq 0 -a -e "$dir/flagman-old.dump"
expect "the loop does not report a failed dump as done" test "$(grep -c 'done' <<<"$out")" -eq 0

rm -f "$shims/fail"
run --loop
expect "the loop waits a day after a good dump" test "$(cat "$shims/slept")" = 86400 -a "$(dumps)" -eq 1

# -mtime counts whole days: kept for 7 days means younger than 7 * 24 hours stays.
rm -rf "$dir"
mkdir "$dir"
now=$(date +%s)
touch -d "@$((now - 7 * 86400 + 3600))" "$dir/flagman-young.dump"
touch -d "@$((now - 7 * 86400 - 3600))" "$dir/flagman-aged.dump"
code=0
out=$(PATH="$shims:$PATH" BACKUP_DIR="$dir" BACKUP_KEEP_DAYS=7 sh "$script" 2>&1) || code=$?
expect "a dump younger than the kept days stays" test -e "$dir/flagman-young.dump"
expect "a dump just past the kept days goes" test ! -e "$dir/flagman-aged.dump"

exit "$failed"
