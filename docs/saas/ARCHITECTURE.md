# Wealtharr multi-tenant architecture and migration plan

Status: living plan. **Revision 2 (owner decision): the tenant is the user.** There is no Organization or
Membership. Wherever the sections below say "organization" or "membership", read "the owning user";
"roles" are replaced by configurable **plans** (section 11). Each user's records are theirs alone,
the owner stays the platform admin, and sharing between users is not a goal. Sections 3.1 to 3.3 and 4
describe the original organization design and are kept for the reasoning; section 11 and the updated
ladder in section 10 are what is built.
Scope: the seven workstreams in the owner's brief (tenancy, auth lifecycle, billing, admin separation,
security, reliability, compliance scaffolding). Legal, regulatory, tax and privacy obligations are the
operator's responsibility and are out of scope here; this document covers engineering only.

## 1. Goals and non-goals

Goals
- Many customers on one deployment, each seeing only their own data, with no code path that can return
  another tenant's rows.
- Self-serve registration, subscription billing and plan gating from one canonical entitlement service.
- Platform operator tooling that is separate from customer tooling and fully audited.
- The existing domain engine stays as it is: ledger folds, versioned strategies, quote-evidence gates,
  append-only history, Decimal money. It moves behind a service layer; it is not rewritten.

Non-goals (for now)
- Schema-per-tenant or database-per-tenant.
- Native mobile apps (only the entitlement abstraction that will let them plug in later).
- Changing any strategy rule, instrument mapping or price-safety gate.

## 2. What is in the code today (findings that shape the plan)

- Single-owner by convention: every user-owned row has `owner -> auth.User`, and views filter with
  `owner=request.user` (76 occurrences across 22 modules). There is no tenant object, no role system.
- `Account`, `StrategyInstance`, `WealthSnapshot`, `NotificationDelivery` carry `owner` directly.
  The ledger family (`LedgerEntry`, `LedgerRevision`, `PriceMark`, `StrategySignal`, `StrategyBaseline`,
  `StrategyValueSnapshot`, `RebalanceExecution`, `SignalFill`, `BrokerImportRow`, `StrategyGoLive`,
  `InstanceOpeningSnapshot`, `OpeningReconciliation`) is reachable only through `strategy`.
- Shared market data is already global and deduplicated (`MarketObservation`, `StudyPrice`,
  `DividendEvent`, `PriceRevision`, `YahooSync`): one fetch per symbol, not per user. This is the right
  shape for multi-tenancy and stays global.
- `SystemIntegration` is a singleton (pk=1) holding SMTP and the shared Discord/Telegram bots. That is a
  platform integration, not tenant data. Per-user Discord webhooks are encrypted per row.
- Encryption: one Fernet key (`DATA_ENCRYPTION_KEY`, else `SECRET_KEY`) for all ciphertext.
- Auth: Django sessions, Turnstile on login, per-visitor lockout, optional per-user TOTP
  (`OwnerTOTP`, enforced by `RequireOwnerTOTP`). Admin is Django admin gated on `is_staff`.
  Accounts are created by hand; `InviteRequest` only collects waitlist emails.
- Operations: Docker compose (db, web, reminders), `/health`, backup verify/restore scripts, hourly
  `sync_market_and_signals` plus notification commands, `RateLimitBucket` for limits.
- Domain logic is already pure where it matters (`strategies/<name>/<version>.py::evaluate`), but the
  plug-in modules and many views reach into the ORM directly. A service layer has to be introduced
  gradually around them.

## 3. Target architecture

```
                      +--------------------- platform scope ---------------------+
 browser --> web ---> |  catalogue (StrategyPreset)   market data (global tables)  |
   |         |        |  SystemIntegration            audit log   plan catalogue   |
   |         v        +-------------------------------------------------------------+
   |   tenancy middleware  -->  TenantContext (org, membership, role)  [fail closed]
   |         |
   |         v
   |   application services (ledger, evaluation, notification, entitlement, export)
   |         |      \--> domain engine (pure, unchanged)
   |         v
   |   tenant-scoped managers (organization filter required)  -->  PostgreSQL + RLS
   v
 Stripe webhooks --> billing app --> Subscription state --> EntitlementService
```

### 3.1 Tenancy model

- `Organization` is the tenant and the billing boundary. `Membership(user, organization, role)` links
  people to it. Roles: `owner`, `admin`, `member`, `viewer` (viewer is read-only).
- Default product shape (see open question Q1): registering creates a personal Organization with the
  user as `owner`. Sharing with a partner or adviser is "invite a member", added later without a schema
  change.
- A user can belong to several organizations; the active one is chosen per session and stored on the
  request context, never taken from a URL or form field without a membership check.
- `owner -> User` stays on the existing models for authorship and so the domain code keeps working.
  Tenancy is a separate `organization` column.

### 3.2 Data classification

| Class | Models | Rule |
|---|---|---|
| Tenant-owned, direct | `Account`, `StrategyInstance`, `WealthSnapshot`, `NotificationDelivery` | `organization` FK, NOT NULL |
| Tenant-owned, via strategy | ledger family listed in section 2 | `organization` denormalised from the strategy, enforced equal |
| Per-user | `NotificationPreference`, `OwnerTOTP` | belong to the user, not the tenant |
| Platform-global | `StrategyPreset`, `MarketObservation`, `StudyPrice`, `DividendEvent`, `PriceRevision`, `YahooSync`, `SystemIntegration`, `NotificationSchedulerRun`, `RateLimitBucket`, `InviteRequest` | no tenant column; platform admins only |
| New, platform | `Organization`, `Membership`, `Plan`, `Subscription`, `StripeEvent`, `EntitlementOverride`, `AuditEvent`, `DeletionRequest` | see modules |

### 3.3 Isolation, three layers

1. **Application**: every tenant-owned model gets `objects = TenantManager()`. Its queryset requires an
   active `TenantContext` and raises if none is set. `Model.all_tenants` is a separate, explicitly named
   manager used only by platform code and background jobs that loop over tenants. Forgetting a filter
   therefore fails loudly instead of leaking.
2. **Database**: PostgreSQL row-level security on every tenant-owned table, keyed on
   `current_setting('app.org_id')`, set with `SET LOCAL` at the start of each request/transaction. The app
   connects as a role without `BYPASSRLS`; migrations and platform jobs use a separate role. Policies
   cover SELECT, INSERT, UPDATE and DELETE. A trigger (or `WITH CHECK`) keeps the denormalised
   `organization_id` on child rows equal to their strategy's.
3. **Tests**: an isolation harness that creates two tenants, walks every URL pattern in `v2_urls` and the
   public ones, and asserts tenant B never receives tenant A's objects (404, not 403). Part of CI; any new
   route must be registered with it or the test fails.

SQLite stays development-only; RLS tests run against Postgres in CI.

### 3.4 Service layer (strangler pattern)

New package `tracker/app/` with small services: `LedgerService` (record, correct, replay, export),
`EvaluationService` (gather immutable history and approved quotes, call the pure strategy, store the
standard result), `NotificationService`, `EntitlementService`, `ExportService`. Views call services;
services take a `TenantContext`. Existing helpers (`ledger_summary`, `effective_entries`,
`verified_observations`, `price_evidence`) are wrapped, not rewritten. Views are converted slice by slice
(overview, today, ledger, review, history, settings), each behind the isolation harness.

### 3.5 Background jobs

The hourly job loops over tenants with `all_tenants`, but each unit of work runs inside
`tenant_context(org)` so RLS and managers apply. Per-tenant failures are isolated and logged with
`org_id`. Market sync stays global and runs once. Jobs skip tenants whose entitlement is lapsed (grace
period honoured) and never fetch data for tenants with no live strategies.

## 4. Migration strategy from the single-owner model

Every step is a separate migration and PR, reversible until the final constraint step, and verified with
CI running migrations twice plus a restore of a production-shaped snapshot.

1. **Add** `Organization`, `Membership`, `AuditEvent` (new tables only). No behaviour change.
2. **Add nullable** `organization` to tenant-owned tables.
3. **Backfill** (data migration, idempotent, audited): for each existing user with data create one
   Organization named after the user, `Membership(role=owner)`, set `organization` on every owned row and
   on every child row via its strategy. Write an `AuditEvent` per organization. A verification command
   compares row counts and ledger hashes before and after (same ledger digests the app already computes).
4. **Dual scope**: managers accept the new filter while the old `owner` filter keeps running, so a mismatch
   is detected rather than silently changing results. Shadow-assert in tests and in a staging run.
5. **Enforce**: NOT NULL, unique constraints re-keyed to `(organization, ...)`, RLS policies, tenant
   managers become mandatory. Removing the `owner` filter from views happens slice by slice.
6. **Keep the existing login working**: the migrated owner keeps username, password and TOTP. The new
   registration flow creates new users; nothing about an existing session changes.

Rollback: steps 1 to 4 are additive and can be reverted by dropping the new columns; step 5 is preceded by
a full database backup and a documented restore rehearsal.

## 5. Modules

Each module ships as one or more PRs with tests; the isolation harness gates all of them.

### M1 Tenancy core
Organization, Membership, roles, tenant context middleware, managers, backfill, isolation harness.
Exit: two-tenant harness green; existing owner data byte-identical in ledger digests.

### M1b Audit log (before anything privileged is built)
Append-only `AuditEvent(actor, organization, action, target, metadata, ip, request_id, prev_hash, hash)`.
Hash chain gives tamper evidence; the app DB role has INSERT but not UPDATE/DELETE. A single
`audit.record()` call is the only writer.

### M2 Authentication and account lifecycle
Self-serve registration (Turnstile, rate-limited), email verification with signed single-use tokens,
password reset, TOTP with recovery codes, session list and revoke-others, change email/password with
re-authentication, account deletion: soft delete with a grace period, data export (extends the existing
ZIP export into a restorable bundle), then a purge job that removes tenant rows and writes a deletion
receipt. Recommendation: build on Django's built-in primitives and the existing hardened login rather than
adding a large auth framework (see Q2). Existing `OwnerTOTP` secrets and lockout are kept.

### M3 Entitlements, then billing
`Plan` (code, limits, features), `Subscription` (organization, plan, status, period end, grace end, source),
`EntitlementOverride` (support-granted, time-boxed, audited). **One** `EntitlementService`
(`can()`, `limit()`, `snapshot()`) is the only place plan logic lives; views and jobs ask it and never
read plan names. Gated capabilities are expressed as keys (`strategies.max`, `channels.discord`,
`study.simulate`, `history.years`, `members.max`, `export.full`).
Stripe: Checkout and Customer Portal for changes, **webhooks as the source of truth** (verified signature,
idempotent `StripeEvent` table with event id, replay-safe, out-of-order tolerant by comparing Stripe's
object version/period). Invoices are Stripe-hosted and linked, not copied. Dunning uses Stripe's retry
schedule; the app maps `past_due` to a configurable grace window with banner, then downgrade to the
Free entitlement set (read-only above the free limits, never deleting data). Future stores: a
`SubscriptionSource` interface (`stripe`, `apple`, `google`, `manual`) writes into the same
`Subscription`/entitlement resolution, with an explicit precedence and a reconciliation job; only the
interface and tests ship now.
Fail-closed rule: if entitlement state cannot be resolved, the tenant gets the Free set, never more.

### M4 Admin separation
- Platform admin: a separate `AdminSite` on its own path, `is_platform_staff` group, mandatory TOTP,
  optional IP allowlist. It shows tenant *metadata* (plan, status, counts, last sync) and never ledger
  contents. Reading tenant data requires a time-boxed, reason-bearing, audited support grant that the
  tenant sees in their audit view.
- Tenant admin: role `owner`/`admin` inside the app (members, billing portal link, alert destinations,
  export, delete account). No Django admin access.
- Today's `admin.py` registrations for `LedgerEntry`, `Account`, `StrategyInstance` are removed from the
  platform admin or replaced by metadata-only views.
- Every privileged action calls `audit.record()`; a test enumerates admin actions and fails if one doesn't.

### M5 Security hardening
- Encryption boundary: envelope encryption. Per-tenant data key (DEK) wrapped by a platform key (KEK from
  a secrets manager or file today, KMS later); ciphertext carries `kid`. Rotation command re-wraps DEKs
  without touching data and a second command re-encrypts under a new DEK. `SECRET_KEY_FALLBACKS` for
  Django secret rotation. Existing single-key ciphertext migrates through the existing
  `migrate_data_encryption` path.
- Rate limits: extend `RateLimitBucket` to per-IP and per-account on registration, login, password reset,
  verification resend, and financial POSTs (ledger add/edit, fills), with distinct, documented limits.
- CSRF/CORS: no cross-origin API is exposed, so CORS stays off. Webhook endpoints are CSRF-exempt only
  with signature verification. Cookie, HSTS and CSP review recorded in the threat model.
- CI: `pip-audit`, secret scanning (gitleaks), `bandit`, CodeQL, dependency pinning and SBOM artefact.
- Threat model document (`docs/saas/THREAT_MODEL.md`): assets, trust boundaries, STRIDE per boundary,
  tenant-isolation abuse cases, mitigations and residual risk.

### M6 Reliability and operations
- Backups: whole-database base backup plus WAL for point-in-time recovery. Per-tenant backup is a
  restorable logical bundle (the export, extended with ledger revisions and signals); per-tenant restore
  loads the bundle into a scratch database, verifies ledger digests, then imports through the services
  (never raw SQL) so append-only and audit rules still apply. DR runbook with RPO/RTO targets and a
  scheduled restore drill.
- Logging: JSON structured logs with `request_id`, `org_id`, `user_id`; never amounts, tokens or emails.
- Health: `/healthz` (process alive), `/readyz` (database reachable, migrations applied, required secrets
  present: fails closed), `/healthz/integrations` (price-feed freshness, SMTP, Stripe, Discord/Telegram:
  reports degradation, never fails readiness).
- CI: migrations run twice on an empty database and once on a restored previous-release snapshot;
  end-to-end Playwright flow signup, verification, subscription (signed Stripe test webhook), strategy
  activation, calculation, notification (captured by an in-memory backend).

### M7 Compliance scaffolding
Terms of service, privacy policy, DPA template, retention and deletion policy, security disclosure page and
`security.txt`, public methodology note on the calculation engine in non-advisory language. These are
drafts for counsel to edit; the code ships the mechanics (consent records with version and timestamp,
policy version pinned per account, deletion and export flows, retention jobs).

## 6. Sequencing

1. This plan and the `CLAUDE.md` amendment (below).
2. M1 tenancy core, then M1b audit log.
3. Service-layer slices behind the harness (overview, today, ledger, review, history, settings).
4. M2 authentication lifecycle.
5. M3 entitlements (with manual plans), then Stripe.
6. M4 admin separation.
7. M5 security hardening and M6 operations, partly in parallel from step 3 (CI scanning, health,
   logging can start immediately).
8. M7 compliance scaffolding drafts early, finalised last.

Nothing in 5 to 7 is public-facing until M1, M1b, M2 and M4 are complete.

## 7. Engineering constraints that stay as they are

Pure `evaluate(ledger, prices, params, as_of)`; versioned immutable strategy definitions; pinned
instrument mapping per instance; append-only ledger, signals, price evidence and version events; Decimal
money; fail closed on missing, stale, held or unverified data; no invented prices; transactions atomic with
rollback tests; each account tracked in its broker's currency.

One new engineering dependency, not a blocker: the price feed is behind a `PriceProvider` interface
(today a Yahoo adapter). Counsel decides which provider and licence apply at launch; the code only needs
the seam so the provider can change without touching evaluation. Each provider records whether its output
is cleared for display, and the engine already refuses unverified quotes.

## 8. Proposed `CLAUDE.md` amendment

The current file forbids billing, public signup and account-global mutable state ("one person uses the app
now"). Those lines would be replaced with: multi-tenant by `Organization`; every tenant-owned query through
tenant managers; billing and signup allowed only through the entitlement and auth modules described here;
the strategy, quote-evidence and append-only rules unchanged.

## 9. Decisions and open questions

Decided by the owner:
- Q1 Personal Organization per signup; invite-a-member comes later.
- Q2 Django primitives plus the existing hardened login (no django-allauth).
- Q3 Single-host Docker compose for the first public cohort: second DB role for RLS, file-based
  secrets, WAL-based backups; the key provider stays pluggable so a KMS can replace the file later.
- Q5 Keep the current price feed for the beta; the `PriceProvider` seam is built so it can be replaced.

Still open:

Q4 Plans: which capabilities differ between Free and paid tiers, what are the prices and currencies, and
is Stripe Tax wanted?
Q6 Notification channels: keep the shared platform Discord/Telegram bots and SMTP (proposed), with
per-tenant webhooks staying tenant-owned?
Q7 Existing production data: is the owner's account the only one, so the backfill creates exactly one
organization?
Q8 Data residency: any region requirement for the database and backups?
Q9 Do all strategies appear on every plan, or are some gated?

## 10. Going slowly: ground rules and the PR ladder

Ground rules (owner's brief: "exactly how it is today, but multi-tenant", owner stays admin)
- **The product does not change.** Every step leaves the owner's pages, URLs, wording and numbers as they
  are. Each PR is checked with the full Python and browser suites, and the ledger digests printed by
  `manage.py tenancy_check --digest` must be identical before and after.
- **The owner stays the platform admin** (superuser) throughout, with the same login and authenticator.
  Existing data becomes the owner's personal organization; nothing is re-entered.
- **No door opens early.** Self-serve registration stays off (`SELF_SERVE_SIGNUP`, default off) until the
  isolation work, audit log and admin separation are done. Until then new accounts are still created by
  hand, now with a role.
- **Two kinds of permission.** Platform: the owner only (admin site, support grants, plans). Tenant:
  `owner`, `admin`, `member`, `viewer` per organization; a viewer can look but not record anything.
- **Small, revertible PRs**, each independently safe to stop at.

PR ladder (revision 2)
1. This plan and the `CLAUDE.md` amendment (done).
2. Tenancy foundation (done, then simplified to per-user; the organization tables were removed).
3. Audit log (done).
4. Read paths move to tenant managers keyed on the user, one slice at a time (strategy lookups done),
   each with two-tenant isolation tests.
5. Write paths and background jobs run inside a tenant context.
6. Enforce: row-level security keyed on the owner id, on a second database role.
7. Plans: features defined in code, plans and users configured in the admin, one `can()` check
   (section 11).
8. Platform admin split from ordinary users, with audited support access.
9. Auth lifecycle (registration, verification, reset, sessions, deletion and export) behind the flag.
10. Stripe webhooks that assign plans automatically, and a user-facing plans page.
11. Security hardening, operations and compliance scaffolding, partly in parallel from step 4.

## 11. Plans (replaces organization roles)

- **Features** are keys declared in code (`tenancy.features.register(...)`), each with a label and a
  description. Adding one is one declaration plus one `can()` call where it is enforced.
- **Plans** are created in the admin by the owner (Admin → Plans): name, description, price text, Stripe
  and PayPal links (https only), which features it includes, optional numeric limits, whether it is public,
  and whether it is the default. A built-in plan "Full" grants every feature, including ones added later,
  so existing users see no change. The links are plain links; the app does not learn from them that
  someone paid, so plans are assigned by hand until a Stripe webhook is added.
- **Per user** (Admin → Users): the owner edits users by hand and sets one plan plus optional feature
  grant/deny overrides on the same page, or assigns a plan to several users at once.
- **One check** (`tenancy.access.can(user, key)`, `limit(user, key)`) and a view decorator. Platform
  admins (superusers) always pass. An unknown feature or an unresolvable plan denies (fail closed).
- Changes to plans, to users and to their plans are written to the audit log.
