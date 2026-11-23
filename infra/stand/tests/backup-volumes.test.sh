#!/usr/bin/env bash
# Tests backup-volumes.sh against a fake docker.
set -euo pipefail

script="$(cd "$(dirname "$0")/../bin" && pwd)/backup-volumes.sh"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
root="$tmp/root"
shims="$tmp/shims"
mkdir -p "$root" "$shims"
touch "$root/stand.env"

cat >"$shims/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
d=$(dirname "$0")
printf '%s\n' "$*" >>"$d/calls"
if [[ "${1:-}" == volume && "${2:-}" == inspect ]]; then
  exit 0
fi
if [[ "${1:-}" == run ]]; then
  backup_dir=""
  output=""
  previous=""
  for arg; do
    if [[ "$previous" == -v && "$arg" == */backup ]]; then
      backup_dir="${arg%:/backup}"
    fi
    if [[ "$arg" == /backup/* ]]; then
      output="${arg##*/}"
    fi
    previous="$arg"
  done
  test -n "$backup_dir"
  test -n "$output"
  touch "$backup_dir/$output"
  exit 0
fi
exit 1
EOF
chmod +x "$shims/docker"

PATH="$shims:$PATH" STAND_ROOT="$root" bash "$script"
test "$(grep -c '^volume inspect flagman_' "$shims/calls")" -eq 3
test "$(grep -c '^run ' "$shims/calls")" -eq 3
test "$(find "$root/volume-backups" -maxdepth 1 -name 'flagman_*.tar.gz' | wc -l)" -eq 3
test "$(find "$root/volume-backups" -maxdepth 1 -name '.partial-*' | wc -l)" -eq 0
printf 'ok   backup-volumes archives caddy and pgbackup volumes\n'
