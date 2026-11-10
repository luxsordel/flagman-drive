#!/usr/bin/env bash
# Tests for scripts/scan-status-issue.sh against a fake gh, so no network and no real issues.
# Usage: bash scripts/tests/scan-status-issue.test.sh
# The fake `gh issue list` prints the "<number>\t<title>" lines given to check() and fails when given
# "FAIL"; every other gh call is appended to a log. The expected regex must match a line of that log;
# an empty expectation means gh made no other call.
set -euo pipefail

script="$(cd "$(dirname "$0")/.." && pwd)/scan-status-issue.sh"
shims=$(mktemp -d)
trap 'rm -rf "$shims"' EXIT

cat >"$shims/gh" <<'EOF'
#!/usr/bin/env bash
d=$(dirname "$0")
case "$1 $2" in
  "issue list")
    [ "$(cat "$d/listed")" = FAIL ] && { echo "HTTP 502" >&2; exit 1; }
    printf '%b' "$(cat "$d/listed")" ;;
  "issue comment" | "issue create" | "issue close") echo "gh $*" >>"$d/log" ;;
  *) echo "unexpected gh call: $*" >&2; exit 1 ;;
esac
EOF
chmod +x "$shims/gh"

title="Weekly dependency scan failed"
url="https://github.com/o/r/actions/runs/1"
failed=0
# check <name> <expected exit> <listed issues> <expected gh call regex, "" for none> <args...>
check() {
  local name=$1 want_code=$2 listed=$3 want_log=$4 code log
  shift 4
  printf '%s' "$listed" >"$shims/listed"
  : >"$shims/log"
  code=0
  PATH="$shims:$PATH" bash "$script" "$@" >/dev/null 2>&1 || code=$?
  log=$(cat "$shims/log")
  if [ "$code" -eq "$want_code" ] && if [ -z "$want_log" ]; then [ -z "$log" ]; else grep -Eq "$want_log" <<<"$log"; fi; then
    echo "ok   $name"
  else
    echo "FAIL $name: exit $code (want $want_code), gh calls:"
    printf '%s\n' "$log"
    failed=1
  fi
}

check "failure without an open issue creates one" 0 "" \
  "^gh issue create --title $title --label security --body .*$url" failure "$url"
check "failure with an open issue comments on it" 0 "12\t$title" \
  "^gh issue comment 12 --body .*$url\$" failure "$url"
check "only an exact title counts as the open issue" 0 "7\t$title in the stand\n12\t$title" \
  "^gh issue comment 12 " failure "$url"
check "an issue that only mentions the scan is left alone" 0 "7\tWhy the $title issue is noisy" \
  "^gh issue create " failure "$url"
check "success closes the open issue" 0 "12\t$title" \
  "^gh issue close 12 --comment .*$url\$" success "$url"
check "success without an open issue does nothing" 0 "" "" success "$url"
check "a cancelled run neither opens nor closes" 0 "12\t$title" "" cancelled "$url"
check "a failed issue lookup fails the step" 1 "FAIL" "" failure "$url"
check "a missing run url is a usage error" 1 "" "" failure

exit "$failed"
