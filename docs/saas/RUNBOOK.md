# Operator runbook

## Is it up?
- `GET /health/` — process and database answer (use for liveness).
- `GET /ready/` — database, all migrations applied, a default plan exists (use for readiness and after deploys). 503 names the failing check.
- Every response carries `X-Request-ID`; send your own through the proxy and it appears on every log line and in audit events.

## Logs
Plain text by default; `LOG_FORMAT=json` writes one JSON object per line (`time, level, logger, message, request_id`). `LOG_LEVEL` sets verbosity. Secrets are not logged.

## Deploy checklist
1. Back up (`wealtharr.sh backup`, or your usual database dump) and copy `python manage.py tenancy_check --digest` output.
2. Pull, `migrate`, then `tenancy_check` (ownership consistent, a default plan exists) and compare the digests: they must be identical.
3. Check `/ready/`. Admin → Plans should show the default plan.
4. After a deploy that adds tables and if row-level security is on: re-run `rls_apply`.

## One user's data
- `python manage.py user_bundle export <username> -o file.json` writes that user's records, ids and encrypted secrets included. Treat the file like a database backup.
- `python manage.py user_bundle restore file.json` loads it into a database that does not already have those rows or that username; it refuses otherwise and is all-or-nothing. It does not merge or overwrite.
- A user can download their own readable export at `/account/` (no secrets).

## Rotating the data-encryption key
Put the old key in `DATA_ENCRYPTION_KEYS_PREVIOUS`, set the new `DATA_ENCRYPTION_KEY`, restart, run `migrate_data_encryption`, check with `migrate_data_encryption --check` (should say 0), then remove the old key.

## Not covered here
Restore drills of the whole database (do them regularly), monitoring and paging, and the legal side of running the service.

## Scale notes
The hourly job handles users one after another, each inside their own scope. Price downloads are shared, not per user: each symbol is fetched at most once every 12 hours however many people hold it. What grows with users is the database work of reviewing each strategy; measure it on your own data (the "Yahoo monitoring" line and the scheduler run on the Admin page) before assuming you need to parallelise.
