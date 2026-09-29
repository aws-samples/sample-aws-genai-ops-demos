#!/usr/bin/env bash
# Shared helpers for /opt/aurora-demo scripts (run ON the bastion).
# Sourced by inject / rollback / status / seed-data.
ENV_FILE="/opt/aurora-demo/env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: $ENV_FILE not found. Re-run deploy-all from your laptop." >&2
  exit 1
fi
# shellcheck disable=SC1090
source "$ENV_FILE"

# Fetch DB credentials from Secrets Manager (bastion role has read access)
_SECRET_JSON="$(aws secretsmanager get-secret-value --secret-id "$SECRET_ARN" \
  --region "$REGION" --query SecretString --output text 2>/dev/null)"
if [[ -z "$_SECRET_JSON" ]]; then
  echo "ERROR: could not read DB secret $SECRET_ARN in $REGION" >&2
  exit 1
fi
export DB_USER="$(echo "$_SECRET_JSON" | jq -r .username)"
export MYSQL_PWD="$(echo "$_SECRET_JSON" | jq -r .password)"

mysql_w() { mysql --connect-timeout=10 -h "$WRITER_ENDPOINT" -P "$DB_PORT" -u "$DB_USER" "$DB_NAME" "$@"; }
mysql_r() { mysql --connect-timeout=10 -h "$READER_ENDPOINT" -P "$DB_PORT" -u "$DB_USER" "$DB_NAME" "$@"; }

PIDDIR="/opt/aurora-demo/pids"
mkdir -p "$PIDDIR"

record_pid() { echo "$1" >> "$PIDDIR/$2.pids"; }

kill_scenario() {
  local name="$1"
  local f="$PIDDIR/$name.pids"
  [[ -f "$f" ]] || { echo "  (no active $name)"; return 0; }
  while read -r pid; do
    [[ -n "$pid" ]] || continue
    pkill -TERM -P "$pid" 2>/dev/null || true
    kill -TERM "$pid" 2>/dev/null || true
  done < "$f"
  rm -f "$f"
  echo "  stopped: $name"
}
