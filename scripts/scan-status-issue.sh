#!/usr/bin/env bash
# Keeps one open issue for a failing weekly scan (.github/workflows/scheduled-scans.yml): a failed run
# opens it or comments on it, the next passing run closes it. A scheduled run belongs to nobody's PR,
# so without the issue its failure only reaches whoever last edited the cron line.
# Usage: scan-status-issue.sh <result of the scans job> <run url>
# Needs gh with GH_TOKEN (issues: write) and GH_REPO, as the workflow sets them.
set -euo pipefail

title="Weekly security scan failed"
result=${1:?usage: scan-status-issue.sh <result> <run url>}
run_url=${2:?usage: scan-status-issue.sh <result> <run url>}

# Search is fuzzy, so the title is matched exactly: an issue that only mentions the scan stays untouched.
open_issue=$(gh issue list --state open --label security --search "in:title \"$title\"" \
  --json number,title --jq '.[] | "\(.number)\t\(.title)"' |
  awk -F '\t' -v t="$title" '$2 == t && !found { print $1; found = 1 }')

case "$result" in
  failure)
    if [ -n "$open_issue" ]; then
      gh issue comment "$open_issue" --body "Снова упал: $run_url"
    else
      gh issue create --title "$title" --label security --body "$(
        cat <<EOF
Еженедельный скан зависимостей и Semgrep упал: $run_url

Открыть запуск и найти упавший job: pip-audit, npm audit или Semgrep. Обновить зависимость или исправить
находку по правилам \`docs/03-security.md\`, раздел «Зависимости и CI». Issue закроется сама, когда следующий
запуск по расписанию или ручной запуск \`scheduled-scans\` пройдёт.
EOF
      )"
    fi
    ;;
  success)
    if [ -n "$open_issue" ]; then
      gh issue close "$open_issue" --comment "Скан снова проходит: $run_url"
    fi
    ;;
  *)
    # A cancelled run says nothing about the dependencies, so it neither opens nor closes the issue.
    echo "Scan result: $result, issue left as is."
    ;;
esac
