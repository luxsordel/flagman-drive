#!/usr/bin/env bash
# Tests for scripts/npm-audit.mjs on canned `npm audit --json` reports, so no network.
# Usage: bash scripts/tests/npm-audit.test.sh
# The script runs from a scratch copy of the repository layout, so each case sets its own allowlist.
set -euo pipefail

source_script="$(cd "$(dirname "$0")/.." && pwd)/npm-audit.mjs"
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
mkdir -p "$root/scripts" "$root/.github"
cp "$source_script" "$root/scripts/npm-audit.mjs"

failed=0

# check <name> <allowlist json> <expected exit code> <expected output fragment>; the report comes on stdin.
check() {
  local name=$1 allowlist=$2 want_code=$3 want_text=$4 out code=0
  printf '%s' "$allowlist" >"$root/.github/npm-audit-allowlist.json"
  out=$(node "$root/scripts/npm-audit.mjs" 2>&1) || code=$?
  if [ "$code" -ne "$want_code" ] || [[ "$out" != *"$want_text"* ]]; then
    echo "FAIL $name: exit $code (want $want_code), output:"
    echo "$out"
    failed=1
  else
    echo "ok   $name"
  fi
}

counts() { echo "{\"info\":0,\"low\":0,\"moderate\":0,\"high\":$1,\"critical\":0,\"total\":$1}"; }
clean="{\"auditReportVersion\":2,\"vulnerabilities\":{},\"metadata\":{\"vulnerabilities\":$(counts 0)}}"
high='{"auditReportVersion":2,"vulnerabilities":{"tar":{"name":"tar","severity":"high","via":[
  {"source":1,"name":"tar","severity":"high","title":"Path traversal",
   "url":"https://github.com/advisories/GHSA-aaaa-bbbb-cccc"}]}},
  "metadata":{"vulnerabilities":'"$(counts 1)"'}}'

check "a clean report passes" '[]' 0 'no unapproved' <<<"$clean"
check "an unlisted high advisory fails" '[]' 1 'FAIL  high tar GHSA-aaaa-bbbb-cccc' <<<"$high"
# reviewBy may lie at most 92 days ahead; dates come from node, as GNU and BSD date differ.
days_ahead() { node -e "console.log(new Date(Date.now() + $1 * 864e5).toISOString().slice(0, 10))"; }
soon=$(days_ahead 30)
entry() { echo "[{\"id\":\"GHSA-aaaa-bbbb-cccc\",\"package\":\"tar\",\"reason\":\"Only unpacks our own archives\",$1}]"; }

check "a listed high advisory passes until its review date"   "$(entry "\"reviewBy\":\"$soon\"")" 0 'allow high tar' <<<"$high"
check "an expired exception fails"   "$(entry '"reviewBy":"2000-01-01"')" 1 'expired' <<<"$high"
check "a stale exception fails"   "$(entry "\"reviewBy\":\"$soon\"")" 1 'no longer reported' <<<"$clean"

# The script reads its own clock, so the exact 92/93-day boundary holds only if no UTC midnight
# passes between computing the dates here and running the script. A run that straddles midnight
# is discarded and repeated rather than reported, as its verdict compares dates from two days.
boundary_cases() {
  check "an exception may run for 92 days"     "$(entry "\"reviewBy\":\"$(days_ahead 92)\"")" 0 'allow high tar' <<<"$high"
  check "an exception for more than 92 days fails"     "$(entry "\"reviewBy\":\"$(days_ahead 93)\"")" 1 'reviewBy must be at most 92 days ahead' <<<"$clean"
}
for _ in 1 2; do
  day=$(days_ahead 0)
  boundary_code=0
  boundary_out=$(failed=0; boundary_cases; exit "$failed") || boundary_code=$?
  [ "$(days_ahead 0)" = "$day" ] && break
done
echo "$boundary_out"
[ "$boundary_code" -eq 0 ] || failed=1

# Malformed entries fail even when no advisory is reported, so a bad exception never waits for one.
check "an exception without a reason fails"   "[{\"id\":\"GHSA-aaaa-bbbb-cccc\",\"package\":\"tar\",\"reviewBy\":\"$soon\"}]" 1 'reason' <<<"$high"
check "an exception with a blank reason fails"   "[{\"id\":\"GHSA-aaaa-bbbb-cccc\",\"package\":\"tar\",\"reason\":\" \",\"reviewBy\":\"$soon\"}]" 1 'reason' <<<"$high"
check "an exception without a package fails"   "[{\"id\":\"GHSA-aaaa-bbbb-cccc\",\"reason\":\"x\",\"reviewBy\":\"$soon\"}]" 1 'package' <<<"$high"
check "an exception with a non-GHSA id fails"   "[{\"id\":\"CVE-2024-1234\",\"package\":\"tar\",\"reason\":\"x\",\"reviewBy\":\"$soon\"}]" 1 'GHSA' <<<"$clean"
check "an exception with a malformed date fails"   "$(entry '"reviewBy":"2026-13-01"')" 1 'reviewBy must be a date' <<<"$high"
check "an exception with an impossible date fails"   "$(entry '"reviewBy":"2026-02-30"')" 1 'reviewBy must be a date' <<<"$high"
check "an exception without a date fails" "$(entry '"note":1')" 1 'reviewBy must be a date' <<<"$high"
check "a duplicate exception fails"   "[$(entry "\"reviewBy\":\"$soon\"" | tr -d '[]'),$(entry "\"reviewBy\":\"$soon\"" | tr -d '[]')]"   1 'listed twice' <<<"$high"
check "an allowlist that is not a list fails" '{}' 1 'must be a JSON array' <<<"$clean"

# What npm audit prints when the advisory endpoint is unreachable: valid JSON, but not a report.
check "a registry error fails instead of passing as clean" '[]' 1 'not an npm audit report' <<'EOF'
{
  "message": "request to http://127.0.0.1:9/-/npm/v1/security/advisories/bulk failed, reason: connect ECONNREFUSED 127.0.0.1:9",
  "error": {
    "summary": "",
    "detail": ""
  }
}
EOF
check "a report without severity totals fails" '[]' 1 'not an npm audit report' <<<'{"vulnerabilities":{}}'
check "JSON null fails" '[]' 1 'not an npm audit report' <<<'null'
check "an empty pipe fails" '[]' 1 "Expected \`npm audit --json\` output" </dev/null
check "high totals without a parsed advisory fail" '[]' 1 'none could be read' \
  <<<"{\"auditReportVersion\":2,\"vulnerabilities\":{},\"metadata\":{\"vulnerabilities\":$(counts 2)}}"

exit "$failed"
