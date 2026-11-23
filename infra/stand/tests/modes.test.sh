#!/usr/bin/env bash
# The stand scripts are run directly (docs/08), so git must store them as executable. On Windows a plain
# chmod is invisible to git (core.fileMode=false), which is why the index mode is checked, not the file.
# Usage: bash infra/stand/tests/modes.test.sh
set -euo pipefail

root="$(cd "$(dirname "$0")/../../.." && pwd)"
failed=0
for script in deploy.sh home-up.sh init-env.sh backup-volumes.sh restore.sh seed.sh; do
  mode="$(git -C "$root" ls-files -s "infra/stand/bin/$script" | cut -d' ' -f1)"
  if [ "$mode" != 100755 ]; then
    echo "infra/stand/bin/$script is committed as ${mode:-untracked}, want 100755:" >&2
    echo "  git update-index --chmod=+x infra/stand/bin/$script" >&2
    failed=1
  fi
done
exit "$failed"
