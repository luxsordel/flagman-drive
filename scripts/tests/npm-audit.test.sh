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
check "a listed high advisory passes until its review date" \
  '[{"id":"GHSA-aaaa-bbbb-cccc","package":"tar","reviewBy":"2999-01-01"}]' 0 'allow high tar' <<<"$high"
check "an expired exception fails" \
  '[{"id":"GHSA-aaaa-bbbb-cccc","package":"tar","reviewBy":"2000-01-01"}]' 1 'expired' <<<"$high"
check "a stale exception fails" \
  '[{"id":"GHSA-aaaa-bbbb-cccc","package":"tar","reviewBy":"2999-01-01"}]' 1 'no longer reported' <<<"$clean"

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
