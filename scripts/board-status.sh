#!/usr/bin/env bash
# Move an issue on the team board (user project #1 of the repo owner) to a Status column.
# Usage: scripts/board-status.sh <issue-number> "<Backlog|Ready|In progress|Review|Done>"
# Used by the Claude Code skills in .claude/skills; works in Git Bash on Windows.
set -euo pipefail

issue="${1:?issue number required}"
status="${2:?status required}"
project=1

# GitHub calls go through a flaky network for part of the team: retry before giving up.
gh_retry() {
  local attempt out
  for attempt in 1 2 3 4 5; do
    if out=$(gh "$@" 2>&1); then
      printf '%s' "$out"
      return 0
    fi
    sleep $((attempt * 2))
  done
  echo "gh $* failed: $out" >&2
  return 1
}

owner=$(gh_retry repo view --json owner --jq .owner.login)
pid=$(gh_retry project view "$project" --owner "$owner" --format json --jq .id)
fid=$(gh_retry project field-list "$project" --owner "$owner" --format json \
  --jq '.fields[] | select(.name == "Status") | .id')
opt=$(gh_retry project field-list "$project" --owner "$owner" --format json \
  --jq ".fields[] | select(.name == \"Status\") | .options[] | select(.name == \"$status\") | .id")
if [ -z "$opt" ]; then
  echo "No status column named \"$status\" on the board" >&2
  exit 1
fi

item=$(gh_retry project item-list "$project" --owner "$owner" --limit 500 --format json \
  --jq ".items[] | select(.content.number == $issue) | .id")
if [ -z "$item" ]; then
  repo=$(gh_retry repo view --json nameWithOwner --jq .nameWithOwner)
  item=$(gh_retry project item-add "$project" --owner "$owner" \
    --url "https://github.com/$repo/issues/$issue" --format json --jq .id)
fi

gh_retry project item-edit --project-id "$pid" --id "$item" --field-id "$fid" --single-select-option-id "$opt" >/dev/null
echo "#$issue -> $status"
