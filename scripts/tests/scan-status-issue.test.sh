#!/usr/bin/env bash
# Tests for scripts/scan-status-issue.sh against a fake gh, so no network and no real issues.
# Usage: bash scripts/tests/scan-status-issue.test.sh
# The fake `gh issue list` records its arguments and prints the "<number>\t<title>" lines given to
# check(), or fails when given "FAIL". Every other gh call goes to a log, each followed by an
# end-of-call line, since an issue body spans several lines.
set -euo pipefail

script="$(cd "$(dirname "$0")/.." && pwd)/scan-status-issue.sh"
shims=$(mktemp -d)
trap 'rm -rf "$shims"' EXIT

cat >"$shims/gh" <<'EOF'
#!/usr/bin/env bash
d=$(dirname "$0")
case "$1 $2" in
  "issue list")
    echo "$*" >"$d/list-args"
    [ "$(cat "$d/listed")" = FAIL ] && { echo "HTTP 502" >&2; exit 1; }
    printf '%b' "$(cat "$d/listed")" ;;
  "issue comment" | "issue create" | "issue close") printf 'gh %s\n<end of call>\n' "$*" >>"$d/log" ;;
  *) echo "unexpected gh call: $*" >&2; exit 1 ;;
esac
EOF
chmod +x "$shims/gh"

title="Weekly security scan failed"
url="https://github.com/o/r/actions/runs/1"
failed=0

# The log holds no call when none is expected, else exactly one, and that one matches the regex.
calls_match() {
  local want=$1 log=$2 calls
  calls=$(grep -c '^<end of call>$' <<<"$log" || true)
  if [ -z "$want" ]; then
    [ "$calls" -eq 0 ]
  else
    [ "$calls" -eq 1 ] && grep -Eq "$want" <<<"$log"
  fi
}

# check <name> <expected exit> <listed issues> <expected gh call regex, "" for none> <args...>
check() {
  local name=$1 want_code=$2 listed=$3 want_call=$4 code log
  shift 4
  printf '%s' "$listed" >"$shims/listed"
  : >"$shims/log"
  : >"$shims/list-args"
  code=0
  PATH="$shims:$PATH" bash "$script" "$@" >/dev/null 2>&1 || code=$?
  log=$(cat "$shims/log")
  if [ "$code" -eq "$want_code" ] && calls_match "$want_call" "$log"; then
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

# A closed issue or one without the label is not the scan's issue, whatever its title.
: >"$shims/listed"
PATH="$shims:$PATH" bash "$script" failure "$url" >/dev/null 2>&1
if grep -Eq -- '--state open( |$)' "$shims/list-args" && grep -Eq -- '--label security( |$)' "$shims/list-args"; then
  echo "ok   the lookup asks only for open issues labelled security"
else
  echo "FAIL the lookup asks only for open issues labelled security: $(cat "$shims/list-args")"
  failed=1
fi

exit "$failed"
