#!/usr/bin/env bash
# Owner-only deployment helper. Run as: bash wealtharr.sh COMMAND [argument].
# Never source this file into an interactive SSH shell.
set -Eeuo pipefail
cd "$(dirname -- "${BASH_SOURCE[0]}")"
umask 077
PROJECT=wealtharr_v2

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
require_files() {
  local f
  for f in "$@"; do [[ -s "$f" ]] || die "Missing or empty $f"; done
}
docker_proxy() {
  [[ -s .env ]] && grep -Eq '^TRAEFIK_DOCKER_NETWORK=[^[:space:]#]+' .env
}
compose() {
  local args=(-p "$PROJECT" -f compose.yml)
  if docker_proxy; then args+=(-f compose.traefik.yml); fi
  if [[ -s secrets/data_encryption_key ]]; then args+=(-f compose.data-key.yml); fi
  docker compose "${args[@]}" "$@"
}
preflight() {
  require_files .env.production secrets/postgres_password secrets/django_secret_key
  if docker_proxy; then require_files .env; fi
  compose config --quiet
  if docker_proxy; then
    compose config --format json | python3 -c '
import json, sys
ports = json.load(sys.stdin)["services"]["web"].get("ports")
assert not ports, f"Unsafe web port publication: {ports}"
print("Traefik networking: no published web port")
'
  fi
}
backup_snapshot() {
  # Mandatory, isolated full-restore drill before any production update.
  preflight
  compose exec -T db pg_isready -U wealtharr -d wealtharr >/dev/null ||
    die "Production database is not ready; refusing update without a backup."
  mkdir -p backups
  chmod 700 backups
  local stamp completed pending
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  completed="backups/preupdate-${stamp}.dump"
  pending="${completed}.tmp"
  [[ ! -e "$completed" && ! -e "$pending" ]] ||
    die "Backup already exists for this timestamp; refusing overwrite."
  if ! compose exec -T db pg_dump -U wealtharr -d wealtharr -Fc > "$pending"; then
    rm -f "$pending"
    die "Database export failed; no update attempted."
  fi
  if ! compose exec -T db pg_restore --list < "$pending" >/dev/null; then
    rm -f "$pending"
    die "Invalid database archive; no update attempted."
  fi
  mv -- "$pending" "$completed"
  (cd backups && sha256sum "$(basename "$completed")" > "$(basename "$completed").sha256")
  chmod 600 "$completed" "$completed.sha256"
  bash scripts/verify_backup.sh "$completed" ||
    die "Backup failed isolated full-restore check. Update has NOT started."
  BACKUP_CREATED="$completed"
  echo "Verified pre-update backup: $completed"
}

start() {
  preflight
  compose up -d --build --wait --wait-timeout 180
  compose exec -T web python -m scripts.healthcheck
  compose exec -T web python manage.py migrate --check
  compose ps
}

case "${1:-help}" in
  setup)
    # Only for a truly fresh checkout, not an existing data volume.
    [[ ! -e .env && ! -e .env.production ]] ||
      die "Configuration exists. Use start/update, not setup."
    if docker volume inspect "${PROJECT}_wealtharr_db" >/dev/null 2>&1; then
      die "Existing database volume. Refusing to generate a new key."
    fi
    [[ ! -e secrets/postgres_password && ! -e secrets/django_secret_key && ! -e secrets/data_encryption_key ]] ||
      die "Secret files exist. Refusing to rotate either key."
    read -r -p "Private hostname (e.g. app.example.com): " HOST
    read -r -p "Traefik in Docker? [Y/n]: " DOCKER_PROXY
    if [[ -z "$DOCKER_PROXY" || "$DOCKER_PROXY" =~ ^[Yy]$ ]]; then
      read -r -p "Existing Traefik Docker network: " NET
      read -r -p "Existing TLS resolver name: " RESOLVER
      docker network inspect "$NET" >/dev/null ||
        die "Docker network not found: $NET"
    else
      NET="" RESOLVER=""
      echo "Host-mode Traefik: web binds 127.0.0.1:8088 only."
    fi
    read -r -p "Cloudflare Turnstile site key: " TS_SITE
    read -r -s -p "Cloudflare Turnstile secret key: " TS_SECRET
    printf '\n'
    export HOST NET RESOLVER TS_SITE TS_SECRET
    python3 - <<'PY'
import os, re
from pathlib import Path
host = os.environ["HOST"]
if not re.fullmatch(r"(?=.{4,253}$)[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", host):
    raise SystemExit("Invalid hostname")
for key in ("TS_SITE", "TS_SECRET"):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{10,255}", os.environ[key]):
        raise SystemExit(f"Missing/invalid {key}")
net, resolver = (os.environ[k] for k in ("NET", "RESOLVER"))
if net:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", net) or not re.fullmatch(r"[A-Za-z0-9_.-]+", resolver):
        raise SystemExit("Invalid network/resolver")
    Path(".env").write_text(
        "COMPOSE_PROJECT_NAME=wealtharr_v2\n"
        f"WEALTHARR_HOST={host}\n"
        f"TRAEFIK_DOCKER_NETWORK={net}\n"
        f"TRAEFIK_CERT_RESOLVER={resolver}\n"
    )
else:
    Path(".env").write_text("COMPOSE_PROJECT_NAME=wealtharr_v2\n")
Path(".env.production").write_text(
    f"DEBUG=0\nALLOWED_HOSTS={host}\n"
    f"CSRF_TRUSTED_ORIGINS=https://{host}\n"
    f"TURNSTILE_SITE_KEY={os.environ['TS_SITE']}\n"
    f"TURNSTILE_SECRET_KEY={os.environ['TS_SECRET']}\n"
)
for name in (".env", ".env.production"):
    Path(name).chmod(0o600)
PY
    unset TS_SECRET
    mkdir -p secrets backups
    chmod 700 secrets backups
    python3 - <<'PY'
import secrets
from pathlib import Path
for name in ("postgres_password", "django_secret_key", "data_encryption_key"):
    p = Path("secrets") / name
    with p.open("x") as out:
        out.write(secrets.token_urlsafe(64))
    p.chmod(0o444)  # Readable by the non-root container UIDs; parent is 0700.
print("Private configuration and secrets created. No secrets printed.")
PY
    preflight
    echo "Now run: bash wealtharr.sh start"
    ;;
  start) start ;;
  enable-data-key)
    require_files .env.production secrets/django_secret_key
    [[ ! -e secrets/data_encryption_key ]] || die "Dedicated encryption key already exists."
    python3 - <<'PY'
import secrets
from pathlib import Path
p = Path("secrets/data_encryption_key")
with p.open("x") as out:
    out.write(secrets.token_urlsafe(64))
p.chmod(0o444)
print("Dedicated key generated; never printed or committed.")
PY
    echo "Back up, rebuild, then run python manage.py migrate_data_encryption in web."
    echo "Do not rotate django_secret_key until re-encryption and restore are verified."
    ;;
  update)
    # Verify a restorable backup before fetching new migrations.
    backup_snapshot
    git pull --ff-only origin main
    start
    ;;
  backup)
    backup_snapshot
    ;;
  backup-offsite)
    [[ $# -eq 3 ]] || die "Usage: bash wealtharr.sh backup-offsite AGE_RECIPIENT user@host:/secure/path/"
    command -v age >/dev/null || die "Install age for off-server encryption."
    command -v scp >/dev/null || die "Install OpenSSH scp for off-server upload."
    recipient="$2"
    remote="$3"
    [[ "$recipient" =~ ^age1[a-z0-9]{20,}$ ]] ||
      die "Provide an age public recipient (never a private key)."
    [[ "$remote" == *:* ]] || die "Specify an SSH destination such as user@host:/secure/backups/"
    backup_snapshot
    encrypted="${BACKUP_CREATED}.age"
    [[ ! -e "$encrypted" ]] || die "Encrypted backup already exists; refusing overwrite."
    age -r "$recipient" -o "${encrypted}.tmp" "$BACKUP_CREATED" ||
      { rm -f "${encrypted}.tmp"; die "Encryption failed; nothing uploaded."; }
    mv -- "${encrypted}.tmp" "$encrypted"
    chmod 600 "$encrypted"
    scp -B -- "$encrypted" "$remote" ||
      die "Encrypted upload failed; check remote destination and retry."
    echo "Encrypted archive uploaded. Keep the age private key OFF the server."
    ;;
  status)
    compose ps
    ;;
  logs)
    compose logs --tail=70 --follow web reminders backups
    ;;
  export)
    preflight
    [[ $# -eq 2 ]] || die "Usage: bash wealtharr.sh export /private/backup-directory"
    dest="$2"
    [[ ! -e "$dest" ]] || die "Destination already exists; no overwrite permitted."
    mkdir -m 700 -p -- "$dest"
    cp .env .env.production "$dest/"
    mkdir -m 700 "$dest/secrets"
    cp secrets/postgres_password secrets/django_secret_key "$dest/secrets/"
    if [[ -s secrets/data_encryption_key ]]; then cp secrets/data_encryption_key "$dest/secrets/"; fi
    chmod 600 "$dest/.env" "$dest/.env.production" "$dest/secrets/"*
    compose exec -T db pg_dump -U wealtharr -d wealtharr -Fc > "$dest/wealtharr.dump.tmp"
    mv "$dest/wealtharr.dump.tmp" "$dest/wealtharr.dump"
    (cd "$dest" && sha256sum wealtharr.dump > wealtharr.dump.sha256)
    bash scripts/verify_backup.sh "$dest/wealtharr.dump"
    echo "Portable private export: $dest"
    echo "SENSITIVE: includes plaintext secrets. Transfer only over encrypted SSH;"
    echo "encrypt the directory before storing it outside a trusted server."
    ;;
  restore-fresh)
    [[ $# -eq 2 ]] || die "Usage: bash wealtharr.sh restore-fresh /private/backup-directory"
    src="$2"
    [[ -d "$src" ]] || die "Missing export directory"
    for f in .env .env.production secrets/postgres_password secrets/django_secret_key wealtharr.dump wealtharr.dump.sha256; do
      [[ -s "$src/$f" ]] || die "Missing $src/$f"
    done
    if docker volume inspect "${PROJECT}_wealtharr_db" >/dev/null 2>&1; then
      die "Existing database volume: refusing to overwrite. Restore needs an empty new server."
    fi
    [[ ! -e .env && ! -e .env.production && ! -e secrets/postgres_password ]] ||
      die "Target already configured; refusing to overwrite private settings."
    bash scripts/verify_backup.sh "$src/wealtharr.dump"
    read -r -p "Restore into a NEW EMPTY database on this server? Type RESTORE NEW: " YES
    [[ "$YES" == "RESTORE NEW" ]] || die "No changes made."
    cp "$src/.env" "$src/.env.production" ./
    mkdir -p secrets backups
    chmod 700 secrets backups
    cp "$src/secrets/"* secrets/
    chmod 600 .env .env.production
    chmod 444 secrets/postgres_password secrets/django_secret_key
    preflight
    compose up -d --wait db
    compose exec -T db pg_restore -U wealtharr -d wealtharr \
      --no-owner --no-acl --exit-on-error < "$src/wealtharr.dump"
    start
    echo "Portable restore completed on the new server."
    ;;
  *)
    cat <<'USAGE'
Wealtharr owner commands (run from your checked-out directory):
  bash wealtharr.sh setup            First-time private configuration
  bash wealtharr.sh enable-data-key  Generate separate credential encryption key
  bash wealtharr.sh start            Build, migrate, start, verify
  bash wealtharr.sh update           Verify backup, pull main, rebuild and migrate
  bash wealtharr.sh backup           Create and verify a local PostgreSQL archive
  bash wealtharr.sh backup-offsite AGE_RECIPIENT SSH_DEST   Encrypted upload via age + scp
  bash wealtharr.sh status           See container health
  bash wealtharr.sh logs             Follow relevant logs
  bash wealtharr.sh export DIR       Private portable DB + settings + secret export
  bash wealtharr.sh restore-fresh DIR Restore on a NEW EMPTY server, never over live DB
USAGE
    ;;
esac
