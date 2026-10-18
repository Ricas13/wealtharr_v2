#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "$#" -lt 1 ]]; then
  echo "Usage: WEALTHARR_CONFIRM_RESTORE=YES_REPLACE_WEALTHARR bash scripts/restore_backup.sh ./backups/FILE.dump" >&2
  exit 2
fi
backup="$1"
if [[ ! -f "$backup" ]]; then
  echo "Backup archive not found" >&2
  exit 2
fi
if [[ "$(printenv WEALTHARR_CONFIRM_RESTORE || true)" != "YES_REPLACE_WEALTHARR" ]]; then
  echo "Refusing destructive restore without WEALTHARR_CONFIRM_RESTORE=YES_REPLACE_WEALTHARR" >&2
  exit 3
fi
if [[ "$backup" != *.dump ]]; then
  echo "Select a .dump PostgreSQL custom archive" >&2
  exit 4
fi
command -v docker >/dev/null || { echo "Docker is required" >&2; exit 5; }
echo "Stopping Wealtharr web, hourly scheduler and backups."
docker compose stop web reminders backups
restart() { docker compose start web reminders backups >/dev/null 2>&1 || true; }
trap restart EXIT
echo "Restoring Wealtharr PostgreSQL from selected backup. Current data will be overwritten."
docker compose exec -T db pg_restore -U wealtharr -d wealtharr --clean --if-exists --no-owner --no-acl --exit-on-error < "$backup"
echo "Restore completed. Services restarting."
