#!/usr/bin/env bash
# Full restore drill in an isolated, disposable PostgreSQL 16 instance.
# NEVER connects to the production database or networks.
set -Eeuo pipefail
if [[ $# -ne 1 || ! -f "${1:-}" || "$1" != *.dump ]]; then
  echo "Usage: bash scripts/verify_backup.sh ./backups/wealtharr-YYYYMMDDTHHMMSSZ.dump" >&2
  exit 2
fi
archive=$1
command -v docker >/dev/null || { echo "Docker is required" >&2; exit 2; }
if [[ -f "$archive.sha256" ]]; then
  (cd "$(dirname "$archive")" && sha256sum -c "$(basename "$archive").sha256" >/dev/null) ||
    { echo "Backup checksum mismatch: refusing recovery" >&2; exit 3; }
elif [[ "${WEALTHARR_ALLOW_LEGACY_BACKUP:-}" != "YES" ]]; then
  echo "No checksum sidecar; refuse unverified legacy archive (set WEALTHARR_ALLOW_LEGACY_BACKUP=YES only after manual trust verification)" >&2
  exit 3
fi
# The archive is copied over stdin, so even chmod 0600 host archives can be
# verified by the isolated, nonroot postgres process without a host bind mount.
# No database host, network, SMTP or application secrets are passed through.
docker run --rm -i \
  --network none --user postgres --read-only \
  --tmpfs /tmp:rw,nosuid,size=1073741824 \
  --env PGDATA=/tmp/wealtharr_disposable_pgdata \
  --entrypoint /bin/sh postgres:16-alpine -ec '
    umask 077
    cat > /tmp/restore.dump
    pg_restore --list /tmp/restore.dump >/dev/null
    initdb --username=postgres --auth=trust --pgdata="$PGDATA" >/dev/null
    pg_ctl --pgdata="$PGDATA" --options="-c listen_addresses= -c unix_socket_directories=/tmp" --wait start >/dev/null
    trap "pg_ctl --pgdata=$PGDATA --mode=immediate --wait stop >/dev/null 2>&1 || true" EXIT
    createdb -h /tmp -U postgres wealtharr_restore_drill
    pg_restore -h /tmp -U postgres -d wealtharr_restore_drill \
      --no-owner --no-acl --exit-on-error /tmp/restore.dump
    test "$(psql -h /tmp -U postgres -d wealtharr_restore_drill -Atqc \
      "SELECT count(*) FROM information_schema.tables WHERE table_schema=chr(112)||chr(117)||chr(98)||chr(108)||chr(105)||chr(99) AND table_name=chr(100)||chr(106)||chr(97)||chr(110)||chr(103)||chr(111)||chr(95)||chr(109)||chr(105)||chr(103)||chr(114)||chr(97)||chr(116)||chr(105)||chr(111)||chr(110)||chr(115)")" = "1"
    echo "Disposable PostgreSQL restore completed; Django migration table present."
  ' < "$archive"
echo "Archive checksum and isolated full restore: VERIFIED (no production data changed)."
