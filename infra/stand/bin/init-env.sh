#!/usr/bin/env bash
# Create the local stand environment outside the repository.
#   infra/stand/bin/init-env.sh <domain>
set -euo pipefail
umask 077

repo_root="$(cd "$(dirname "$0")/../../.." && pwd)"
stand_root="${STAND_ROOT:-$HOME/flagman-stand}"
env_file="$stand_root/stand.env"
credentials_file="$stand_root/stand-credentials.txt"
template="$repo_root/infra/stand/stand.env.example"

domain="${1:?usage: init-env.sh <domain>}"
if [[ ! "$domain" =~ ^[A-Za-z0-9.-]+$ || "$domain" != *.* || "$domain" == .* || "$domain" == *. ]]; then
  echo "error: domain must be a DNS name" >&2
  exit 1
fi

mkdir -p "$stand_root"
if [[ ! -f "$env_file" ]]; then
  cp "$template" "$env_file"
fi
chmod 600 "$env_file"

generate_hex() { openssl rand -hex "$1"; }
generate_base64() { openssl rand -base64 "$1" | tr -d '\n'; }
generate_totp() {
  python3 - <<'PY'
import base64
import secrets
print(base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')[:32])
PY
}
generate_code() {
  python3 - <<'PY'
import secrets
while True:
    value = f"{secrets.randbelow(1_000_000):06d}"
    if len(set(value)) > 3 and value not in ("0123456789", "9876543210"):
        print(value)
        break
PY
}

# Values are generated only when the key is absent or empty. Existing settings survive reruns.
set_if_empty() {
  local key="$1" value="$2" tmp
  if grep -q "^${key}=" "$env_file" && grep -q "^${key}=[^[:space:]]" "$env_file"; then
    return 0
  fi
  tmp="$(mktemp "$stand_root/.stand.env.XXXXXX")"
  awk -v key="$key" -v value="$value" '
    $0 ~ ("^" key "=") { print key "=" value; found=1; next }
    { print }
    END { if (!found) print key "=" value }
  ' "$env_file" >"$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$env_file"
}

# Domain is an input, but an existing domain is never silently replaced.
existing_domain="$(sed -n 's/^STAND_DOMAIN=//p' "$env_file" | tail -n 1)"
if [[ -n "$existing_domain" && "$existing_domain" != "$domain" ]]; then
  echo "error: $env_file already belongs to another domain" >&2
  exit 1
fi
set_if_empty STAND_DOMAIN "$domain"
set_if_empty STAND_REGISTRY local
set_if_empty ACME_EMAIL "${ACME_EMAIL:-stand@$domain}"
set_if_empty API_WORKERS 2
set_if_empty API_KEEP_ALIVE_SECONDS 75
set_if_empty DB_POOL_SIZE 10
set_if_empty DB_MAX_OVERFLOW 10
set_if_empty POSTGRES_SUPERUSER_PASSWORD "$(generate_hex 24)"
set_if_empty POSTGRES_OWNER_PASSWORD "$(generate_hex 24)"
set_if_empty POSTGRES_PASSWORD "$(generate_hex 24)"
set_if_empty REDIS_PASSWORD "$(generate_hex 24)"
set_if_empty API_JWT_SECRET "$(generate_hex 32)"
set_if_empty API_OTP_PEPPER "$(generate_hex 32)"
set_if_empty API_TOTP_ENCRYPTION_KEY "$(generate_base64 32)"
set_if_empty SMS_FIXED_CODE "$(generate_code)"
set_if_empty SMS_FIXED_PHONES '+375291111111,+375291111112'
set_if_empty S3_ACCESS_KEY "$(generate_hex 16)"
set_if_empty S3_SECRET_KEY "$(generate_hex 32)"
set_if_empty PAYMENTS_WEBHOOK_SECRET "$(generate_hex 32)"
set_if_empty PAYMENTS_TOKEN_ENCRYPTION_KEY "$(generate_base64 32)"
set_if_empty EMULATOR_COMMAND_SECRET "$(generate_hex 32)"
set_if_empty EMULATOR_TELEMETRY_SECRET "$(generate_hex 32)"
set_if_empty STAND_ADMIN_EMAIL 'admin@stand.local'
set_if_empty STAND_ADMIN_PASSWORD "$(generate_hex 18)"
set_if_empty STAND_ADMIN_TOTP_SECRET "$(generate_totp)"
set_if_empty STAND_OPERATOR_EMAIL 'operator@stand.local'
set_if_empty STAND_OPERATOR_PASSWORD "$(generate_hex 18)"
set_if_empty STAND_OPERATOR_TOTP_SECRET "$(generate_totp)"
set_if_empty STAND_TECHNICIAN_EMAIL 'technician@stand.local'
set_if_empty STAND_TECHNICIAN_PASSWORD "$(generate_hex 18)"
set_if_empty STAND_TECHNICIAN_TOTP_SECRET "$(generate_totp)"
set_if_empty MAP_ORIGINS 'https://tiles.openfreemap.org'

# The credentials file is local-only and is rewritten from the env without printing secrets.
tmp="$(mktemp "$stand_root/.stand-credentials.XXXXXX")"
{
  printf '%s\n' '# Local stand credentials. Keep this file chmod 600 and outside Git.'
  printf 'Domain: %s\n' "$(sed -n 's/^STAND_DOMAIN=//p' "$env_file" | tail -n 1)"
  printf 'Client phones: %s\n' "$(sed -n 's/^SMS_FIXED_PHONES=//p' "$env_file" | tail -n 1)"
  printf 'Client OTP: %s\n' "$(sed -n 's/^SMS_FIXED_CODE=//p' "$env_file" | tail -n 1)"
  for role in ADMIN OPERATOR TECHNICIAN; do
    printf '\n%s\n' "$role"
    sed -n "/^STAND_${role}_EMAIL=/p; /^STAND_${role}_PASSWORD=/p; /^STAND_${role}_TOTP_SECRET=/p" "$env_file"
  done
} >"$tmp"
chmod 600 "$tmp"
mv "$tmp" "$credentials_file"
chmod 600 "$env_file" "$credentials_file"

echo "init-env: environment ready at $env_file"
