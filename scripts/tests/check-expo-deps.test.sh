#!/usr/bin/env bash
# Tests for scripts/check-expo-deps.mjs on canned `expo install --check --json` reports, so no
# network and no Expo CLI. Usage: bash scripts/tests/check-expo-deps.test.sh
set -euo pipefail

script="$(cd "$(dirname "$0")/.." && pwd)/check-expo-deps.mjs"
failed=0

# check <name> <expected exit> <expected output fragment> [--strict]; the report comes on stdin.
check() {
  local name=$1 want_code=$2 want_text=$3 out code=0
  shift 3
  out=$(node "$script" "$@" 2>&1) || code=$?
  if [ "$code" -ne "$want_code" ] || [[ "$out" != *"$want_text"* ]]; then
    echo "FAIL $name: exit $code (want $want_code), output:"
    echo "$out"
    failed=1
  else
    echo "ok   $name"
  fi
}

# report <entries...>, each "<name> <actual> <expected>"; no entries is an up-to-date report.
report() {
  local entries=() name actual expected
  for entry in "$@"; do
    read -r name actual expected <<<"$entry"
    entries+=("{\"packageName\":\"$name\",\"packageType\":\"dependencies\",\"expectedVersionOrRange\":\"$expected\",\"actualVersion\":\"$actual\"}")
  done
  local joined
  joined=$(IFS=,; echo "${entries[*]-}")
  if [ "$#" -eq 0 ]; then
    echo '{"dependencies":[],"upToDate":true}'
  else
    printf '{\n  "dependencies": [%s],\n  "upToDate": false\n}\n' "$joined"
  fi
}

report | check "an up-to-date SDK passes" 0 "match the SDK"
report "expo 58.0.24 ~58.0.25" "expo-location 58.0.21 ~58.0.22" |
  check "packages a patch behind pass with a warning" 0 "WARN  expo@58.0.24"
report "expo 58.0.24 ~58.0.25" | check "the strict run fails on a patch" 1 "FAIL  expo@58.0.24" --strict
report "expo-router 58.0.30 ~58.1.0" | check "a minor mismatch fails" 1 "FAIL  expo-router@58.0.30"
report "expo 57.0.9 ~58.0.25" | check "a major mismatch fails" 1 "FAIL  expo@57.0.9"
# React Native is 0.x: its minor is the breaking part, so the same major.minor rule applies.
report "react-native 0.82.4 0.83.2" | check "a react-native minor mismatch fails" 1 "FAIL  react-native"
report "react-native 0.83.1 0.83.2" | check "a react-native patch passes" 0 "off the SDK by a patch"
# An exact SDK pin is reported for a newer patch too, so the warning must not say "behind".
report "react-native 0.83.3 0.83.2" | check "a newer patch passes" 0 "off the SDK by a patch"
report "expo 58.0.24 ~58.0.25" "expo-router 58.0.30 ~58.1.0" |
  check "one minor mismatch fails a report with patches" 1 "FAIL  expo-router"
report | check "the strict run passes an up-to-date SDK" 0 "match the SDK" --strict
report "expo-router 58.0.30 ~58.1.0" | check "the strict run fails on a minor" 1 "FAIL  expo-router" --strict
report "expo-foo 1.2.3 *" | check "an unreadable expected range fails" 1 "FAIL  expo-foo@1.2.3"
report "expo-foo 1.2.4 ^1.2.5||^2.0.0" | check "an expected list fails" 1 "FAIL  expo-foo@1.2.4"
report "expo-foo latest ~1.2.3" | check "an unreadable actual version fails" 1 "FAIL  expo-foo@latest"
printf '' | check "an empty pipe fails" 1 "gave no report"
printf 'Some log line\n%s\n' "$(report "expo 58.0.24 ~58.0.25")" |
  check "text before the report fails" 1 "gave no report"
echo 'Error: something broke' | check "non-JSON output fails" 1 "gave no report"
echo '{"upToDate":true}' | check "a report without dependencies fails" 1 "gave no report"
echo '{"dependencies":[],"upToDate":false}' | check "a self-contradicting report fails" 1 "gave no report"

exit "$failed"
