# Wealtharr Section I — private Oracle / Traefik deployment and recoverability

Status: deployable configuration committed on PR #11, **not a claim that Oracle has been changed, backup restored, the owner received an email, or TOTP was enrolled**. Financial 9Sig source/ISA verification is separate. No broker API, order placement or public signup exists.

## Network architecture

- **Default host/Traefik setup:** `compose.yml` publishes Gunicorn only to `127.0.0.1:8088` on the Oracle host. PostgreSQL has **no published port**. Route an existing trusted **host-mode** Traefik HTTPS server to `127.0.0.1:8088`; do not expose port 8088 to the internet or make Gunicorn the TLS endpoint.
- **Docker-network Traefik option:** use `docker compose -f compose.yml -f compose.traefik.yml`, with Docker Compose v2.24+ (supports `ports: !reset []`), an already-created trusted external Traefik network, a configured TLS certificate resolver, and a deliberate private/VPN CIDR allowlist. Required variables: `WEALTHARR_HOST`, `TRAEFIK_DOCKER_NETWORK`, `TRAEFIK_CERT_RESOLVER`, `WEALTHARR_ALLOWLIST_CIDRS`. The override **removes** the loopback port and exposes port 8000 only on the existing Traefik Docker network. Always inspect the merged `docker compose config` before `up`. If Traefik sees a Cloudflare proxy address rather than the client IP, configure only trusted forwarded-header proxies; never blindly trust arbitrary `X-Forwarded-For` or use `0.0.0.0/0` to defeat the allowlist.
- A private VPN/Cloudflare Access pre-authentication policy may further restrict the hostname but is external infrastructure, **not automatically installed by this PR**. Keep DNS, certificates, Traefik network/forwarded-header trust and firewall under the owner’s control.
- The login requires password and production Cloudflare Turnstile. There is no registration. Optional time-based authenticator can be enabled from the authenticated security page. Docker health probes run locally and do not bypass owner login.
- Django production security: `DEBUG=0`, exact `ALLOWED_HOSTS`, exact HTTPS `CSRF_TRUSTED_ORIGINS`, HSTS, secure session/CSRF cookies and trusted Traefik TLS forwarding. Disable the public Django debug page. No application or broker secrets in GitHub or image layers.

## First install or migration of existing Oracle deployment

Only do these on the **correct** Wealtharr host/check-out; no tool in the PR deploys it. Before replacing a running version, take an independently retained, verified backup of the current PostgreSQL data and separately secure the existing Django `SECRET_KEY` (which also encrypts saved SMTP/Discord credentials and TOTP secrets).

1. Check the actual running stack, Git branch, current data volume, PostgreSQL password and stable Django `SECRET_KEY`. Do **not** run `docker compose down -v`, rotate the database password, replace `SECRET_KEY` or reinitialize `wealtharr_db` as an installation shortcut.
2. Use `umask 077; mkdir -p secrets backups; chmod 700 secrets backups`. Put the **same existing** PostgreSQL database password (no extra newline if preserving a raw old password) in `secrets/postgres_password` and the **same existing** Django encryption key in `secrets/django_secret_key`. For a new, empty instance, generate each independently with a cryptographically secure random generator; never put either value in Git, Discord, a screenshot or shell output.
3. Local Compose file-backed secrets are bind-mounted into containers as `/run/secrets/...`. Their readability for both the non-root Django UID **10001** and PostgreSQL's UID must be checked after setup. A useful secure host layout is a root-owned, `0700` parent `secrets/` with files readable *only within the trusted container mount*. The precise host file permissions depend on the Compose secrets implementation; **do not grant host-user/world access to the parent directory**. Never copy secret files into a Docker image. `.dockerignore` excludes them.
4. Set `.env.production` to `DEBUG=0`, `ALLOWED_HOSTS=wealtharr.vpn4u.cc`, `CSRF_TRUSTED_ORIGINS=https://wealtharr.vpn4u.cc`, and genuine `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY`. The Compose stack passes database identity and `DATABASE_PASSWORD_FILE` / `SECRET_KEY_FILE` explicitly; no password is interpolated into a URI, so special characters in random passwords are supported.
5. Build and inspect without changing running volumes: `docker compose config --quiet`, `docker compose build`, then `docker compose up -d` (or both Compose files for Docker-network Traefik). The container runs Django with UID 10001, only necessary temporary writable paths, and drops Linux capabilities. Never run a second `reminders` replica or a second independent cron for the old `send_due_notifications` command.
6. Test `docker compose ps`; `docker compose exec -T web python manage.py check --deploy`; `docker compose exec -T web python manage.py migrate --check`; `docker compose exec -T web python manage.py test tracker`; local `/health/`; then `https://wealtharr.vpn4u.cc/health/` through your actual TLS ingress. A working `/health/` is **database reachability**, not proof that quotes, the scheduler, TOTP, notifications or restores work. Confirm the private account can log in, optional TOTP challenge cannot be bypassed, and all public registration remains disabled.
7. Check `docker compose logs --tail=100 web reminders backups` for scheduler and backup outcomes. A second, real, owner-authorised test email/Discord delivery is needed before relying on alerts. Keep failures generic in logs; never log a token, auth code, account balance or full message body.

If moving to the `compose.traefik.yml` option, set `WEALTHARR_HOST` and all proxy variables in the invoking deployment environment. Ensure `docker compose -f compose.yml -f compose.traefik.yml config` contains **no published web ports**; failing that, stop before deploying.

## Backup routine and off-host retention

- Only PostgreSQL is backed up; there is **no broker**, so source-ledger history, exact execution receipts, prices, source IDs and alerts all depend on the database. The Compose `backups` service writes custom `pg_dump -Fc` archives to `./backups`, checks that `pg_restore --list` can parse them, publishes via an atomic rename, writes a SHA-256 `.dump.sha256` sidecar and rotates both after 30 days. Archive and checksum live on a **persistent host mount**, not in Docker ephemeral container storage.
- After each successful daily archive, transfer the archive **and checksum** over an encrypted channel to a separate off-host, access-controlled store. Do not leave the only recovery copy on the Oracle instance. Prevent logs, object keys and backups from becoming public. The encryption/backup tooling, cloud destination and off-host schedule are infrastructure-specific and must be configured and demonstrated by the owner; this PR does not claim they exist.
- Keep the **Django SECRET_KEY** separately encrypted and off-host (along with the PostgreSQL password and deployment settings). The database backup alone cannot decrypt SMTP/Discord/TOTP ciphertext if the stable key is lost. Never include these secrets inside database archives.

## Full, safe verification without touching real data

Run this for a recent `.dump` before you rely on it:

```sh
bash scripts/verify_backup.sh ./backups/wealtharr-YYYYMMDDTHHMMSSZ.dump
```

It checks the SHA-256 sidecar, streams the archive into a throwaway **PostgreSQL 16** instance with **no network**, fully runs `pg_restore`, checks the Django migrations table, exits and destroys the container. It does not read the application configuration or contact the production database. For older archival dumps with **no** matching SHA-256 sidecar only, the operator can explicitly set `WEALTHARR_ALLOW_LEGACY_BACKUP=YES`; independently validate their origin before exercising that compatibility bypass. A parseable archive alone is insufficient. A file larger than the disposable container's 1 GiB `/tmp` capacity needs a revised isolated verification host and should **fail** here rather than silently skipping restore.

To replace a real database **only after** an offline restore succeeds, first take a fresh current backup and confirm that losing all transactions since the selected archive is acceptable:

```sh
WEALTHARR_CONFIRM_RESTORE=YES_REPLACE_WEALTHARR \
  bash scripts/restore_backup.sh ./backups/wealtharr-YYYYMMDDTHHMMSSZ.dump
```

This command is deliberately destructive: it validates the archive in isolation **before** stopping the owner web/scheduler/backup processes, and applies `pg_restore --single-transaction --clean --if-exists --exit-on-error` with the financial database's stable identity. Failure does not mark a partial restore as success. The service restart trap attempts to bring the stack back whether restore succeeds or fails. A real Oracle **restore drill on a disposable environment** must still be carried out by the owner and the restored data must be checked against ledger counts, confirmed resets and scheduler receipts. Nothing in a unit test guarantees an Oracle volume can be restored.

## TOTP and loss of the authenticator

TOTP is optional for the owner; enable it only after securing the recovery plan. Enrol from the authenticated **Security → Authenticator** flow, record the authenticator seed privately, confirm a valid six-digit code and verify that a fresh sign-in requires the second factor. The authenticator seed is encrypted using the stable Django secret and one-time TOTP steps cannot be replayed. Five wrong codes trigger a temporary lock.

If the authenticator is lost and there is no other approved recovery path, do **not** add a public bypass route or clear all users' factors. A trusted operator with console access to the correct Wealtharr database can recover the single verified owner through an explicitly documented, authenticated administrative process, then immediately re-enrol the factor and rotate exposed sessions. This is an operational break-glass process, not a browser self-service bypass.

## Decision gate

Source code and tests prepare the private stack, but **actual readiness requires**: proven Oracle ingress and TLS allowlist, stable secrets and PostgreSQL, the owner account and TOTP test where enabled, scheduler heartbeat, test alert delivery, successful disposable full restore using a real archive, and independently retained off-host copy. If any check is unavailable, report that step as *not verified*, not 'already deployed'. No changes here affect 9Sig arithmetic, Trading 212 products, deposits or fill confirmation.
