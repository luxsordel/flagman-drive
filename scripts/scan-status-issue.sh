#!/usr/bin/env bash
# Keeps one open issue per failing weekly check (.github/workflows/scheduled-scans.yml): a failed run
# opens it or comments on it, the next passing run closes it. A scheduled run belongs to nobody's PR,
# so without the issue its failure only reaches whoever last edited the cron line.
# Usage: scan-status-issue.sh <job result> <run url> [security|expo]
# Needs gh with GH_TOKEN (issues: write) and GH_REPO, as the workflow sets them.
set -euo pipefail

usage="usage: scan-status-issue.sh <job result> <run url> [security|expo]"
result=${1:?$usage}
run_url=${2:?$usage}
case "${3-security}" in
  security)
    title="Weekly security scan failed"
    label=security
    summary="Еженедельный скан зависимостей и Semgrep упал"
    instructions="Открыть запуск и найти упавшее задание pip-audit, npm audit или Semgrep. Обновить зависимость
или исправить находку по разделу «Зависимости и CI» в \`docs/03-security.md\`."
    ;;
  expo)
    title="Weekly Expo SDK check failed"
    label=area:mobile
    summary="Еженедельная строгая проверка Expo SDK упала"
    instructions="Открыть задание Expo SDK compatibility. Если проверка нашла расхождение версий, обновить
пакеты по ADR-0008 командой \`npx expo install --fix\` в \`apps/mobile\`."
    ;;
  *) echo "$usage" >&2; exit 1 ;;
esac

# Search is fuzzy, so the title is matched exactly: an issue that only mentions the scan stays untouched.
open_issue=$(gh issue list --state open --label "$label" --search "in:title \"$title\"" \
  --json number,title --jq '.[] | "\(.number)\t\(.title)"' |
  awk -F '\t' -v t="$title" '$2 == t && !found { print $1; found = 1 }')

case "$result" in
  failure)
    if [ -n "$open_issue" ]; then
      gh issue comment "$open_issue" --body "Снова упал: $run_url"
    else
      gh issue create --title "$title" --label "$label" --body "$(
        cat <<EOF
$summary. Ссылка на запуск: $run_url

$instructions

Задача закроется, когда эта проверка пройдёт в следующем запуске \`scheduled-scans\` на \`main\`.
EOF
      )"
    fi
    ;;
  success)
    if [ -n "$open_issue" ]; then
      gh issue close "$open_issue" --comment "Проверка снова проходит: $run_url"
    fi
    ;;
  *)
    # A cancelled run says nothing about the dependencies, so it neither opens nor closes the issue.
    echo "Scan result: $result, issue left as is."
    ;;
esac
