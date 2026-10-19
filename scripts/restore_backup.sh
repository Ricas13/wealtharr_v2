#!/usr/bin/env bash
# Destructive live recovery is explicitly opt-in, fully preflighted and
# transactionally applied ONLY AFTER an independent offline restore drill.
set -Eeuo pipefail
if [[ $# -ne 1 || ! -f "${1:-}" || "$1" != *.dump ]]; then
  echo "Usage: WEALTHARR_CONFIRM_RESTORE=YES_REPLACE_WEALTHARR bash scripts/restore_backup.sh ./backups/FILE.dump" >&2
  exit 2
fi
if [[ "${WEALTHARR_CONFIRM_RESTORE:-}" != "YES_REPLACE_WEALTHARR" ]]; then
  echo "Refusing destructive database replacement without explicit WEALTHARR_CONFIRM_RESTORE=YES_REPLACE_WEALTHARR" >&2
  exit 3
fi
if [[ ! -f compose.yml || ! -f .env.production || ! -d backups ]]; then
  echo "Refusing: run from the intended Wealtharr deployment root with Compose and private environment" >&2
  exit 4
fi
command -v docker >/dev/null || { echo "Docker is required" >&2; exit 4; }
if ! docker compose config --services | grep -qx db; then
  echo "Refusing: active Compose project has no expected Wealtharr db service" >&2
  exit 4
fi
archive=$1
# The verifier runs a complete restore in an isolated, disposable Postgres
# without a network, NOT against the live DB. Failure stops before downtime.
bash scripts/verify_backup.sh "$archive"
echo "The archive passed isolated full-restore verification."
echo "Stopping web, scheduled reviews and backups before replacing Wealtharr database."
docker compose stop web reminders backups
restart() { docker compose start web reminders backups >/dev/null 2>&1 || true; }
trap restart EXIT
if ! docker compose exec -T db pg_isready -U wealtharr -d wealtharr >/dev/null; then
  echo "Database unavailable; live restoration cancelled" >&2
  exit 5
fi
# A failure rolls back the PostgreSQL transaction, retaining the previous DB.
# No destructive restore is attempted without successful verification above.
docker compose exec -T db pg_restore \
  -U wealtharr -d wealtharr --single-transaction \
  --clean --if-exists --no-owner --no-acl --exit-on-error < "$archive"
echo "Live Wealtharr restore committed transactionally. App and scheduler restart on exit."
