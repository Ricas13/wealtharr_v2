# Portable private Wealtharr deployment

**One directory. One script.** No aliases, editing Compose by hand, or
sourcing shell error options into SSH. Run `bash wealtharr.sh ...` from
your checkout; it picks Compose files and fixes the project name as
`wealtharr_v2`.

## Existing /opt/wealtharr-v2 server

After this change is merged into `main`:

```bash
cd /opt/wealtharr-v2
git pull --ff-only origin main
bash wealtharr.sh start
bash wealtharr.sh status
```

Your previous `.env.compose.yml` and
`COMPOSE_FILE=.env.compose.yml:compose.traefik.yml` are ignored by this
helper: it explicitly selects tracked `compose.yml` plus the proxy
override. The web entrypoint automatically migrates and collects static files;
the helper verifies the database and migration state. It does not delete data
or rotate any encryption key.

Updates are simply `bash wealtharr.sh update`.
For logs use `bash wealtharr.sh logs`.

## New, empty server

Install Docker Compose v2.24+, Git and Python 3, and configure your trusted
Traefik with its shared network and working TLS certificate resolver.

```bash
sudo mkdir -p /opt/wealtharr-v2
sudo chown "$USER:$(id -gn)" /opt/wealtharr-v2
git clone https://github.com/Ricas13/wealtharr_v2.git /opt/wealtharr-v2
cd /opt/wealtharr-v2
bash wealtharr.sh setup
bash wealtharr.sh start
docker compose -p wealtharr_v2 -f compose.yml -f compose.traefik.yml exec web python manage.py createsuperuser
```

The interactive setup asks for your private hostname, proxy network, TLS
resolver, restrictive IP/CIDR allowlist and Cloudflare Turnstile keys. It
generates independent PostgreSQL and stable Django keys without printing
them. For host-mode Traefik choose `n` when prompted, and route your
HTTPS proxy to the local 127.0.0.1:8088 backend instead. Enable optional
TOTP inside Wealtharr and save its recovery seed separately.

**Cloudflare proxy 403:** Traefik generally sees a Cloudflare edge IP rather
than your laptop IP. Only AFTER activating a Cloudflare Zero Trust Access
policy that authenticates your identity for this exact hostname, run:

```bash
bash wealtharr.sh allow-cloudflare
```

Confirm by typing `ACCESS PROTECTED`. This safely downloads and
validates both IPv4 and IPv6 Cloudflare ranges, adjusts the allowlist and
recreates only the app web container. Turnstile alone is **not** Cloudflare
Access. Without Access, use a truly private VPN route and restrictive source
ranges instead. An unauthenticated curl may correctly receive 403.

## Move to a different server

On the working server:

```bash
cd /opt/wealtharr-v2
bash wealtharr.sh export /path/to/private/wealtharr-transfer
```

This makes **one private directory** containing:
- `wealtharr.dump` and `wealtharr.dump.sha256` — a full
  checksum-verified PostgreSQL dump with an isolated recovery test
- `.env`, `.env.production` — domain, proxy and Turnstile settings
- `secrets/postgres_password` and `secrets/django_secret_key` —
  the keys for the database and encrypted integration/TOTP records

Transfer the folder only over encrypted SSH and guard it closely. It includes
**plaintext private secrets**: encrypt the folder before off-site archival;
never store it in a public cloud location, Git or a chat.

On a fresh checkout with NO existing database volume or config:

```bash
cd /opt/wealtharr-v2
bash wealtharr.sh restore-fresh /path/to/private/wealtharr-transfer
```

This checks the archive in a network-isolated temporary PostgreSQL,
requires explicit confirmation, restores the database without overwriting
any live instance, and starts Wealtharr using the original keys. The new
server must already have the referenced Traefik network/resolver and DNS.
If those differ, update the private export copy of `.env` first.
Changing hostname also requires corresponding Turnstile configuration.

The automatic daily Compose backup service still keeps local archives for
30 days. Off-host encryption/transfer is your responsibility; schedule it
and periodically test a restore on an isolated machine.

## Important boundaries

- Never use `docker compose down -v` for updates.
- Healthy deployment does not certify real Trading 212 imports, instrument
  eligibility or independent 9Sig workbook parity.
- External Cloudflare Access, Traefik routing and off-host encrypted storage
  cannot be provisioned or security-certified by this application.
