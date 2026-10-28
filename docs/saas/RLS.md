# Database row-level security (optional second wall)

The app already scopes every tenant query in Python (tenant managers fail closed). This adds a
PostgreSQL rule behind it: a role that owns nothing can only read or write rows belonging to the user
the connection is currently acting for. A forgotten filter then returns nothing instead of leaking.

**It is off by default and changes nothing until you do all of the steps below.**

## Turn it on

1. Back up the database.
2. Create a login role that does **not** own the tables and is not a superuser, e.g.
   `CREATE ROLE wealtharr_app LOGIN PASSWORD '…';`
3. As the table-owner role: `python manage.py rls_apply --role wealtharr_app` (`--dry-run` prints the SQL first).
4. Point the **web and worker** processes at `wealtharr_app` (keep running `migrate` as the owner) and set `RLS_ENABLED=1`.
5. After every `migrate` that adds tables, re-run `rls_apply` (idempotent) so new tables are covered and granted.

Turn it off: unset `RLS_ENABLED` (policies are then inert for the owner role), or `python manage.py rls_apply --remove`.

## What it covers

- Every table in the `tracker` app with an `owner` column is limited to `owner_id = app.tenant_id`; tables reached through a strategy or account are limited to rows whose parent is visible. `rls_apply` prints the tables it leaves open (shared market data, site settings).
- `tenant_context(user)` tells the connection whose data it is; no context means no rows; `platform_context()` means "trusted platform code" and sees everything. Platform admins (superusers), management commands, and the `per_tenant` job loops' initial listing run as platform; each item is then processed as its owner.
- Writes are checked too: inserting a row for another user is refused by the database.

## Limits

- Superusers and the table-owner role bypass RLS by design; it protects against bugs in the app, not against anyone holding the owner credentials. Use `--force` only for single-role setups and run `migrate` as a superuser then.
- Tables outside the `tracker` app (auth, audit, plans) are not row-protected here; they have their own controls.
- Costs two extra statements per request while enabled.

## How it was checked

Beyond the policy tests (which run in CI against PostgreSQL), the whole Python suite was run once against a non-superuser database role with the policies forced on, `RLS_ENABLED=1`, and only code inside a tenant/platform context allowed to see rows. That found and fixed: the sign-in redirect read the user's strategies before the request was theirs, and the platform-admin middleware must keep the admin as the active user while bypassing the database check.
