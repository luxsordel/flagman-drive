#!/usr/bin/env bash
# Wait until someone else acts on a pull request, then print what happened and exit.
# Usage: scripts/wait-pr-activity.sh <pr-number> [--since <UTC time>] [--since-sha <sha>]
#          [--until-review-request] [--max-hours <n>] [--quiet <seconds>]
#   --since                 count only reviews and comments after this moment, e.g. 2026-10-23T10:00:00Z
#                           (default: now). Pass the moment your last round started, so that what the other
#                           side did during the round still counts.
#   --since-sha             count only commits after this one (default: the current head)
#   --until-review-request  reviewer mode: wake only when a review is requested from you, which the author
#                           does once the whole fix is pushed and answered
#   --max-hours             stop waiting after this many hours from the start (default 24)
#   --quiet                 seconds without new activity before waking (default 120)
# Exit codes: 0 activity or the PR closed, 1 GitHub unreachable, 2 bad arguments, 3 nothing happened in time.
# Claude Code runs it in the background (see .claude/skills/review-pr and ship): the exit wakes the
# session, so the reviewer's agent picks up the author's fix and the author's agent picks up a new review
# without a human passing messages between them. Works in Git Bash on Windows.
set -euo pipefail

usage() {
  echo "usage: $0 <pr-number> [--since <YYYY-MM-DDTHH:MM:SSZ>] [--since-sha <sha>] [--until-review-request] [--max-hours <n>] [--quiet <seconds>]" >&2
  exit 2
}

pr="${1:-}"
[[ $pr =~ ^[0-9]+$ ]] || usage
shift
since="" since_sha="" until_request=false max_hours=24
# The other agent usually pushes, then replies in several threads, then re-requests review. Waiting for a
# quiet spell turns that burst into one wake-up instead of a recheck of a half-finished fix.
quiet=120
while [ $# -gt 0 ]; do
  case "$1" in
    --since) since="${2:-}"; shift 2 || usage ;;
    --since-sha) since_sha="${2:-}"; shift 2 || usage ;;
    --until-review-request) until_request=true; shift ;;
    --max-hours) max_hours="${2:-}"; shift 2 || usage ;;
    --quiet) quiet="${2:-}"; shift 2 || usage ;;
    *) usage ;;
  esac
done
# Values end up in arithmetic and API paths, and the script is allowed without a prompt: accept only
# the exact shapes, so nothing passed in can be evaluated.
[[ $max_hours =~ ^[0-9]+$ && $quiet =~ ^[0-9]+$ ]] || usage
[ -z "$since" ] && since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
[[ $since =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] || usage
[[ -z $since_sha || $since_sha =~ ^[0-9a-f]{7,40}$ ]] || usage

poll=60
max_failures=10
err_file=$(mktemp)
trap 'rm -f "$err_file"' EXIT

# GitHub calls go through a flaky network for part of the team: retry before giving up. Stderr stays out of
# the output, so a gh warning cannot break parsing.
gh_retry() {
  local attempt out
  for attempt in 1 2 3 4 5; do
    if out=$(gh "$@" 2>"$err_file"); then
      printf '%s' "$out"
      return 0
    fi
    sleep $((attempt * 2))
  done
  echo "gh $* failed: $(cat "$err_file")" >&2
  return 1
}

me=$(gh_retry api user --jq .login)
repo=$(gh_retry repo view --json nameWithOwner --jq .nameWithOwner)
# A commit whose email is not linked to a GitHub account has no login; the local git email still tells
# our own commits apart from the author's.
my_email=$(git config user.email || true)
# Bot comments (Dependabot, Actions) are not a reply from a teammate and must not wake anyone.
others="select((.login // \"\") as \$l | \$l != \"$me\" and (\$l | test(\"^(dependabot|github-actions)\$|\\\\[bot\\\\]\$\") | not))"

# Prints one line: state head reviews comments review-requested-from-me review-comments; counts are of
# activity by others after --since.
snapshot() {
  local view rc
  view=$(gh_retry pr view "$pr" --json state,headRefOid,reviewRequests,reviews,comments --jq "
    [ .state, .headRefOid,
      ([.reviews[] | select(.submittedAt > \"$since\") | .author | $others] | length),
      ([.comments[] | select(.createdAt > \"$since\") | .author | $others] | length),
      ([.reviewRequests[].login] | index(\"$me\") != null)
    ] | map(tostring) | join(\" \")") || return 1
  # The REST endpoint is the only one that lists replies inside review threads.
  rc=$(gh_retry api "repos/$repo/pulls/$pr/comments" --paginate \
    --jq "[.[] | select(.created_at > \"$since\") | .user | $others] | length") || return 1
  echo "$view $(echo "$rc" | awk '{ s += $1 } END { print s + 0 }')"
}

# Authors of new commits after --since-sha. A /sync from main is not the author's fix: compare lists the
# commits it brought in too, so follow only the first-parent chain of the branch and skip merge commits.
new_commit_authors() {
  gh_retry api "repos/$repo/compare/$since_sha...$1" --jq "
    (.commits | map({key: .sha, value: .}) | from_entries) as \$c
    | [\"$1\" | recurse(\$c[.].parents[0].sha; \$c[.] != null)] | map(\$c[.])
    | [.[] | select((.parents | length) == 1)
      | select((.author.login // \"\") != \"$me\" and .commit.author.email != \"$my_email\")
      | (.author.login // .commit.author.name)] | unique | join(\", \")"
}

start=$(date +%s)
deadline=$((start + max_hours * 3600))
failures=0
last_events=""
last_change=$start

while :; do
  now=$(date +%s)
  if [ "$now" -ge "$deadline" ]; then
    echo "Nothing happened on PR #$pr for $max_hours h since the watch started, stopped waiting"
    exit 3
  fi

  if ! snap=$(snapshot) || [ -z "$snap" ]; then
    failures=$((failures + 1))
    if [ "$failures" -ge "$max_failures" ]; then
      echo "GitHub is unreachable through gh ($failures failed checks in a row), stopped waiting: $(cat "$err_file")"
      exit 1
    fi
    sleep "$poll"
    continue
  fi
  failures=0
  read -r state head reviews comments requested rcomments <<<"$snap"
  [ -n "$since_sha" ] || since_sha=$head

  if [ "$state" != "OPEN" ]; then
    echo "PR #$pr is $state"
    exit 0
  fi
  if [ "$requested" = "true" ]; then
    echo "Review of PR #$pr is requested from $me"
    exit 0
  fi

  if [ "$until_request" = false ]; then
    events=""
    if [ "$head" != "$since_sha" ]; then
      if authors=$(new_commit_authors "$head"); then
        [ -n "$authors" ] && events+="- new commits by $authors, head is now $head"$'\n'
      else
        events+="- head moved to $head and cannot be compared with $since_sha (history rewritten?)"$'\n'
      fi
    fi
    [ "$reviews" -gt 0 ] && events+="- $reviews new review(s)"$'\n'
    [ "$comments" -gt 0 ] && events+="- $comments new PR comment(s)"$'\n'
    [ "$rcomments" -gt 0 ] && events+="- $rcomments new comment(s) in review threads"$'\n'

    if [ "$events" != "$last_events" ]; then
      last_events=$events
      last_change=$now
    elif [ -n "$events" ] && [ $((now - last_change)) -ge "$quiet" ]; then
      echo "Activity on PR #$pr by others since $since:"
      printf '%s' "$events"
      exit 0
    fi
  fi

  sleep "$poll"
done
