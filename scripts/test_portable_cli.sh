#!/usr/bin/env bash
# Simulate a Cloudflare edge feed ending IPv4 WITHOUT a newline, with no real
# Docker/Traefik/Cloudflare changes.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cp wealtharr.sh compose.yml compose.traefik.yml "$tmp/"
mkdir -p "$tmp/secrets" "$tmp/fake-bin"
printf 'dev-db' > "$tmp/secrets/postgres_password"
printf 'dev-django' > "$tmp/secrets/django_secret_key"
printf 'DEBUG=0\n' > "$tmp/.env.production"
printf 'TRAEFIK_DOCKER_NETWORK=test_proxy\nWEALTHARR_ALLOWLIST_CIDRS=192.0.2.1/32\n' > "$tmp/.env"

cat > "$tmp/fake-bin/curl" <<'MOCK_CURL'
#!/bin/sh
case "$*" in
  *ips-v4*)
    printf '173.245.48.0/20\n103.21.244.0/22\n103.22.200.0/22\n103.31.4.0/22\n141.101.64.0/18\n108.162.192.0/18\n190.93.240.0/20\n188.114.96.0/20\n197.234.240.0/22\n198.41.128.0/17'
    ;;
  *ips-v6*)
    printf '2400:cb00::/32\n2606:4700::/32\n'
    ;;
  *) exit 1 ;;
esac
MOCK_CURL
cat > "$tmp/fake-bin/docker" <<'MOCK_DOCKER'
#!/bin/sh
case "$*" in
  *'config --format json'*) printf '{"services":{"web":{"ports":null}}}\n' ;;
  *) exit 0 ;;
esac
MOCK_DOCKER
chmod +x "$tmp/fake-bin/"*

(
  cd "$tmp"
  before="$(sha256sum .env)"
  if printf 'NOT VERIFIED\n' | PATH="$tmp/fake-bin:$PATH" bash wealtharr.sh allow-cloudflare >/dev/null 2>&1; then
    echo "Rejected confirmation was erroneously accepted" >&2
    exit 1
  fi
  [[ "$(sha256sum .env)" == "$before" ]] || {
    echo "Rejected confirmation modified configuration" >&2; exit 1;
  }
  printf 'ACCESS PROTECTED\n' | PATH="$tmp/fake-bin:$PATH" bash wealtharr.sh allow-cloudflare >/dev/null
  grep -q 'WEALTHARR_ALLOWLIST_CIDRS=.*198.41.128.0/17,2400:cb00::/32' .env
  grep -q 'WEALTHARR_ALLOWLIST_CIDRS=.*2606:4700::/32' .env
  grep -q '^WEALTHARR_ALLOWLIST_CIDRS=192.0.2.1/32$' .env.before-cloudflare
)
echo "Cloudflare CLI: newline-safe IPv4/IPv6 parsing and explicit opt-in PASS"
