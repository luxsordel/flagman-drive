#!/usr/bin/env bash
# Tests for scripts/wait-pr-activity.sh against fake gh, sleep and date, so no network and no real waiting.
# Usage: bash scripts/tests/wait-pr-activity.test.sh
# A scenario is one line per poll: "<state> <head> <reviews> <comments> <requested> <thread comments>|<authors>",
# where <authors> is what the compare call returns. State FAIL makes `gh pr view` fail, authors "!" makes
# the compare call fail. Each sleep moves the fake clock one minute and the scenario one line forward.
set -euo pipefail

script="$(cd "$(dirname "$0")/.." && pwd)/wait-pr-activity.sh"
shims=$(mktemp -d)
trap 'rm -rf "$shims"' EXIT

cat >"$shims/gh" <<'EOF'
#!/usr/bin/env bash
d=$(dirname "$0")
line=$(sed -n "$(($(cat "$d/tick") + 1))p" "$d/scenario")
[ -n "$line" ] || line=$(tail -n 1 "$d/scenario")
view=${line%%|*} authors=${line#*|}
case "$1 $2" in
  "api user") echo skyqwer ;;
  "repo view") echo luxsordel/flagman-drive ;;
  "pr view")
    [ "${view%% *}" = FAIL ] && { echo "HTTP 502" >&2; exit 1; }
    echo "${view% *}" ;;
  api\ repos/*/compare/*)
    [ "$authors" = "!" ] && { echo "HTTP 404" >&2; exit 1; }
    echo "$authors" ;;
  api\ repos/*/comments) echo "${view##* }" ;;
  *) echo "unexpected gh call: $*" >&2; exit 1 ;;
esac
EOF
cat >"$shims/sleep" <<'EOF'
#!/usr/bin/env bash
d=$(dirname "$0")
echo $(($(cat "$d/tick") + 1)) >"$d/tick"
EOF
cat >"$shims/date" <<'EOF'
#!/usr/bin/env bash
d=$(dirname "$0")
case "$*" in
  *%s*) echo $((1000000 + $(cat "$d/tick") * 60)) ;;
  *) echo 2026-10-23T10:00:00Z ;;
esac
EOF
chmod +x "$shims/gh" "$shims/sleep" "$shims/date"

failed=0
out=""
# check <name> <expected exit> <expected output regex> <args...>; the scenario is read from stdin.
# Leaves the script's output in $out for extra assertions.
check() {
  local name=$1 want_code=$2 want_out=$3 code
  shift 3
  cat >"$shims/scenario"
  echo 0 >"$shims/tick"
  code=0
  out=$(PATH="$shims:$PATH" bash "$script" "$@" 2>&1) || code=$?
  if [ "$code" -eq "$want_code" ] && grep -Eq "$want_out" <<<"$out"; then
    echo "ok   $name"
  else
    echo "FAIL $name: exit $code, output:"
    while IFS= read -r line; do echo "     $line"; done <<<"$out"
    failed=1
  fi
}
repeat() { for _ in $(seq "$1"); do echo "$2"; done; }

check "a burst of pushes and replies wakes once, with all of it" 0 \
  'new commits by BlackyCGS.*' 58 --since-sha aaaaaaa <<'EOF'
OPEN aaaaaaa 0 0 false 0|
OPEN bbbbbbb 0 0 false 0|BlackyCGS
OPEN bbbbbbb 0 0 false 3|BlackyCGS
OPEN bbbbbbb 0 0 false 3|BlackyCGS
OPEN bbbbbbb 0 0 false 3|BlackyCGS
EOF
grep -q '3 new comment(s) in review threads' <<<"$out" || { echo "FAIL burst lost the thread replies"; failed=1; }
[ "$(cat "$shims/tick")" -eq 4 ] || { echo "FAIL burst woke at tick $(cat "$shims/tick"), want 4"; failed=1; }

check "own pushes and merges from main do not wake, merging does" 0 \
  '^PR #58 is MERGED$' 58 <<'EOF'
OPEN aaaaaaa 0 0 false 0|
OPEN ccccccc 0 0 false 0|
OPEN ccccccc 0 0 false 0|
MERGED ccccccc 0 0 false 0|
EOF

check "reviewer mode ignores pushes and comments until the review is re-requested" 0 \
  'requested from skyqwer' 58 --until-review-request <<'EOF'
OPEN aaaaaaa 0 0 false 0|
OPEN bbbbbbb 0 1 false 2|BlackyCGS
OPEN bbbbbbb 0 1 false 2|BlackyCGS
OPEN bbbbbbb 0 1 false 2|BlackyCGS
OPEN bbbbbbb 0 1 false 2|BlackyCGS
OPEN bbbbbbb 0 1 true 2|BlackyCGS
EOF
[ "$(cat "$shims/tick")" -eq 5 ] || { echo "FAIL re-request woke at tick $(cat "$shims/tick"), want 5"; failed=1; }

check "a review requested before the watch started still wakes" 0 \
  'requested from skyqwer' 58 --until-review-request <<'EOF'
OPEN bbbbbbb 0 0 true 0|
EOF

check "silence ends with a timeout" 3 'stopped waiting' 58 --max-hours 1 \
  < <(repeat 70 "OPEN aaaaaaa 0 0 false 0|")

check "GitHub down for long stops the watch instead of spinning forever" 1 'unreachable' 58 \
  < <(repeat 100 "FAIL x 0 0 false 0|")

check "a short GitHub outage is retried" 0 '1 new review' 58 \
  < <(repeat 8 "FAIL x 0 0 false 0|"; repeat 5 "OPEN aaaaaaa 1 0 false 0|")

check "rewritten history still wakes" 0 'cannot be compared' 58 --since-sha aaaaaaa \
  < <(echo "OPEN aaaaaaa 0 0 false 0|"; repeat 30 "OPEN zzzzzzz 0 0 false 0|!")

check "a non-numeric PR number is rejected" 2 'usage' 'abc' </dev/null
check "arithmetic in an argument is not evaluated" 2 'usage' 58 --max-hours "a[\$(touch $shims/pwned)]" </dev/null
[ ! -e "$shims/pwned" ] || { echo "FAIL --max-hours ran a command"; failed=1; }
check "a malformed --since is rejected" 2 'usage' 58 --since 'yesterday' </dev/null

exit "$failed"
