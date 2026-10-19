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
    [[ ! -e secrets/postgres_password && ! -e secrets/django_secret_key ]] ||
      die "Secret files exist. Refusing to rotate either key."
    read -r -p "Private hostname (e.g. wealtharr.vpn4u.cc): " HOST
    read -r -p "Traefik in Docker? [Y/n]: " DOCKER_PROXY
    if [[ -z "$DOCKER_PROXY" || "$DOCKER_PROXY" =~ ^[Yy]$ ]]; then
      read -r -p "Existing Traefik Docker network: " NET
      read -r -p "Existing TLS resolver name: " RESOLVER
      read -r -p "Allowed source IP/CIDR (NOT 0.0.0.0/0): " CIDRS
      docker network inspect "$NET" >/dev/null ||
        die "Docker network not found: $NET"
    else
      NET="" RESOLVER="" CIDRS=""
      echo "Host-mode Traefik: web binds 127.0.0.1:8088 only."
    fi
    read -r -p "Cloudflare Turnstile site key: " TS_SITE
    read -r -s -p "Cloudflare Turnstile secret key: " TS_SECRET
    printf '\n'
    export HOST NET RESOLVER CIDRS TS_SITE TS_SECRET
    python3 - <<'PY'
import ipaddress, os, re
from pathlib import Path
host = os.environ["HOST"]
if not re.fullmatch(r"(?=.{4,253}$)[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", host):
    raise SystemExit("Invalid hostname")
for key in ("TS_SITE", "TS_SECRET"):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{10,255}", os.environ[key]):
        raise SystemExit(f"Missing/invalid {key}")
net, resolver, cidrs = (os.environ[k] for k in ("NET", "RESOLVER", "CIDRS"))
if net:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", net) or not re.fullmatch(r"[A-Za-z0-9_.-]+", resolver):
        raise SystemExit("Invalid network/resolver")
    nets = [n.strip() for n in cidrs.split(",")]
    if not all(nets):
        raise SystemExit("CIDR allowlist is required")
    for n in nets:
        network = ipaddress.ip_network(n, strict=False)
        if network.prefixlen == 0:
            raise SystemExit("Refusing open-to-all CIDR")
    Path(".env").write_text(
        "COMPOSE_PROJECT_NAME=wealtharr_v2\n"
        f"WEALTHARR_HOST={host}\n"
        f"TRAEFIK_DOCKER_NETWORK={net}\n"
        f"TRAEFIK_CERT_RESOLVER={resolver}\n"
        f"WEALTHARR_ALLOWLIST_CIDRS={','.join(nets)}\n"
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
for name in ("postgres_password", "django_secret_key"):
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
  update)
    git pull --ff-only origin main
    start
    ;;
  status)
    compose ps
    ;;
  logs)
    compose logs --tail=70 --follow web reminders backups
    ;;
  allow-cloudflare)
    docker_proxy || die "Cloudflare allowlist requires Docker-based Traefik."
    read -r -p "Cloudflare Zero Trust Access must ALREADY protect this hostname. Type ACCESS PROTECTED: " YES
    [[ "$YES" == "ACCESS PROTECTED" ]] || die "No changes made."
    command -v curl >/dev/null || die "curl required"
    CIDRS="$(
      { curl -fsS --max-time 20 https://www.cloudflare.com/ips-v4
        printf '\n'
        curl -fsS --max-time 20 https://www.cloudflare.com/ips-v6
        printf '\n'
      } | python3 -c '
import ipaddress, sys
networks = [s.strip() for s in sys.stdin if s.strip()]
if len(networks) < 10:
    raise SystemExit("Incomplete Cloudflare address list")
for value in networks:
    ipaddress.ip_network(value, strict=True)
if not any(":" in n for n in networks) or not any("." in n for n in networks):
    raise SystemExit("Both IPv4 and IPv6 are required")
print(",".join(dict.fromkeys(networks)))
'
    )"
    export CIDRS
    python3 - <<'PY'
import os
from pathlib import Path
p = Path(".env")
rows = p.read_text().splitlines()
key = "WEALTHARR_ALLOWLIST_CIDRS="
if not any(row.startswith(key) for row in rows):
    raise SystemExit("Missing allowlist configuration")
backup = Path(".env.before-cloudflare")
if not backup.exists():
    backup.write_bytes(p.read_bytes())
    backup.chmod(0o600)
rows = [key + os.environ["CIDRS"] if row.startswith(key) else row for row in rows]
p.write_text("\n".join(rows) + "\n")
p.chmod(0o600)
PY
    preflight
    compose up -d --no-deps --force-recreate --wait web
    echo "Cloudflare edge IPs allowed. Cloudflare Access MUST remain enforced."
    echo "Verify protection from an unauthenticated PRIVATE browser window."
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
  bash wealtharr.sh start            Build, migrate, start, verify
  bash wealtharr.sh update           Pull main, rebuild, migrate, verify
  bash wealtharr.sh status           See container health
  bash wealtharr.sh logs             Follow relevant logs
  bash wealtharr.sh allow-cloudflare Allow Cloudflare edge IPs only AFTER Access is enforced
  bash wealtharr.sh export DIR       Private portable DB + settings + secret export
  bash wealtharr.sh restore-fresh DIR Restore on a NEW EMPTY server, never over live DB
USAGE
    ;;
esac
