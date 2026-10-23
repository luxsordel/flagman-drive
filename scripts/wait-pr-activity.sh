#!/usr/bin/env bash
# Wait until someone else acts on a pull request, then print what happened and exit.
# Usage: scripts/wait-pr-activity.sh <pr-number> [max-hours] [quiet-seconds]
# Claude Code runs it in the background (see .claude/skills/review-pr and ship): the exit wakes the
# session, so the reviewer's agent picks up the author's fix and the author's agent picks up a new review
# without a human passing messages between them. Works in Git Bash on Windows.
set -euo pipefail

pr="${1:?pr number required}"
max_hours="${2:-24}"
# The other agent usually pushes, then replies in several threads, then re-requests review. Waiting for a
# quiet spell turns that burst into one wake-up instead of a recheck of a half-finished fix.
quiet="${3:-120}"
poll=60

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

me=$(gh_retry api user --jq .login)
repo=$(gh_retry repo view --json nameWithOwner --jq .nameWithOwner)
# Bot comments (Dependabot, Actions) are not a reply from a teammate and must not wake anyone.
others="select((.login // \"\") as \$l | \$l != \"$me\" and (\$l | test(\"^(dependabot|github-actions)\$|\\\\[bot\\\\]\$\") | not))"

# One line: state head last-commit-author reviews comments review-requested-from-me review-comments
snapshot() {
  local view rc
  view=$(gh_retry pr view "$pr" --json state,headRefOid,reviewRequests,reviews,comments,commits --jq "
    [ .state, .headRefOid, (.commits[-1].authors[0].login // \"unknown\"),
      ([.reviews[].author | $others] | length),
      ([.comments[].author | $others] | length),
      ([.reviewRequests[].login] | index(\"$me\") != null)
    ] | map(tostring) | join(\" \")") || return 1
  # The REST endpoint is the only one that lists replies inside review threads.
  rc=$(gh_retry api "repos/$repo/pulls/$pr/comments" --paginate --jq "[.[].user | $others] | length") || return 1
  echo "$view $(echo "$rc" | awk '{ s += $1 } END { print s + 0 }')"
}

base=$(snapshot)
read -r state0 head0 _ reviews0 comments0 requested0 rcomments0 <<<"$base"
if [ "$state0" != "OPEN" ]; then
  echo "PR #$pr is $state0, nothing to wait for"
  exit 0
fi

events=()
last_event=0
deadline=$(($(date +%s) + max_hours * 3600))

while :; do
  sleep "$poll"
  now=$(date +%s)
  snap=$(snapshot) || continue
  read -r state head author reviews comments requested rcomments <<<"$snap"
  [ -n "$rcomments" ] || continue
  before=${#events[@]}

  if [ "$head" != "$head0" ]; then
    # Our own pushes move the head too; only someone else's commit is news.
    [ "$author" != "$me" ] && events+=("new commits by $author, head is now $head")
    head0=$head
  fi
  [ "$reviews" -gt "$reviews0" ] && events+=("$((reviews - reviews0)) new review(s)")
  [ "$comments" -gt "$comments0" ] && events+=("$((comments - comments0)) new PR comment(s)")
  [ "$rcomments" -gt "$rcomments0" ] && events+=("$((rcomments - rcomments0)) new comment(s) in review threads")
  [ "$requested" = "true" ] && [ "$requested0" = "false" ] && events+=("review re-requested from $me")
  reviews0=$reviews comments0=$comments rcomments0=$rcomments requested0=$requested

  [ ${#events[@]} -gt "$before" ] && last_event=$now

  if [ "$state" != "OPEN" ]; then
    echo "PR #$pr is $state"
    [ ${#events[@]} -eq 0 ] || printf -- '- %s\n' "${events[@]}"
    exit 0
  fi
  if [ ${#events[@]} -gt 0 ] && [ $((now - last_event)) -ge "$quiet" ]; then
    echo "Activity on PR #$pr by others:"
    printf -- '- %s\n' "${events[@]}"
    exit 0
  fi
  if [ "$now" -ge "$deadline" ]; then
    echo "No activity on PR #$pr by others for $max_hours h, stopped waiting"
    exit 0
  fi
done
