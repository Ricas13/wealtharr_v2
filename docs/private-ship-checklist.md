# Wealtharr v2 — Section J private-owner release acceptance

Scope: **one private UK owner**, no public beta, no brokerage connectivity, no
automated trades and no subscription/billing deployment. Reviewed 2026-10-19.
This document distinguishes automated CI evidence from checks that can only
be performed with the owner's actual Trading 212 records or Oracle host.
**Passing synthetic browser tests does not independently validate 9Sig advice.**

## A. Release stages (never skip forward silently)

| Stage | Deliverable | Gate before advancing |
|---|---|---|
| 0. PR #11 code candidate | All Section A–J source committed on one resumable branch | Django regression suite, migrations, production-mode smoke, Docker/Traefik config, synthetic disposable restore and browser jobs **green at the same PR head** |
| 1. Private synthetic rehearsal | Locally access login, onboarding, Today, History, Ledger, Settings and guided actual-fill forms in an isolated test database | Chromium 360px and desktop flows: readable layout, no page-wide horizontal scrolling, usable keyboard skip/navigation, source-price holds, CSV preview refusal, cross-owner isolation, optional TOTP gate, and partial fills never creating a reset |
| 2. Owner-only deployed tracker | Secure Oracle host with HTTPS, private VPN/ingress access control, stable secrets, existing PostgreSQL data preserved, one scheduler and backup services | Owner physically verifies Traefik headers/allowlist, authenticates, runs `manage.py check --deploy`, sees heartbeat, receives deliberately triggered email/Discord tests, and verifies **an actual archive** in isolated PostgreSQL plus an encrypted off-host copy |
| 3. Real portfolio **tracking-only** | Owner imports/reconciles a redacted/actual broker history with exactly mapped GBP securities and true deposits and fills | All missing/duplicate transactions, cash/units/fees and dates reconciled. If unresolved: show **cannot compute**. No guessed position or market price. |
| 4. 9Sig advisory reliance | Version-pinned strategies and approved source quotes on the live account | Original workbook independent quarter-by-quarter comparisons and owner ISA/product permission confirmed; only then interpret a review as potentially actionable. User still places all broker orders separately |
| 5. Routine private use | Quarterly cadence with actual fill confirmation, audited notifications and restore rehearsals | At least one observed normal scheduler cycle, failed-data refusal test, re-enabled/disabled notification checks, recent *recoverable* off-host backup and a planned key-rotation/recovery process |

Stages 2–5 are **not** done by merging a pull request. Stage 4 is **not**
automatically approved just because Stage 3 imports match cash totals.

## B. Automated checks in PR #11

The **Python CI** job runs:
- Django checks, schema migration drift, migrations, and existing financial
  invariants, including no broker API, exact GBP units and owner permissions.
- Production-mode public-page smoke and collectstatic manifest lookup.
- Loopback-only Compose and optional private Traefik override validation,
  secret mounts/configuration and shell syntax.
- Synthetic PostgreSQL dump → checksum → full disposable network-isolated
  restore with the migration table present. This tests the *mechanism*, not
  a recovered **real** Oracle database.

The separate **Chromium CI** job installs pinned Playwright and a real browser,
starts Django's synthetic `StaticLiveServerTestCase`, and tests:
- 360px and 1280px views including Today, History, Ledger, Strategy, Settings;
  semantic headings, focus, skip link, mobile menu and horizontal overflow;
- password sign-in, optional TOTP, protection of private records and
  rejection of cross-owner execution URLs;
- one-screen ISA enrolment with £500/month still **only a plan**, actual dated
  deposit entry, safe CSV preview **without side effects** and duplicate-header
  refusal;
- missing/stale market prices are labelled unavailable and don't offer an
  executable review;
- manually entered partial real-fill *fixtures* write one ledger event, never
  an automatic transaction or a completed 60/40 reset.

The browser runs against a temporary CI database, fixture-only users, and
**no actual Trading 212 connection or credentials**. Chromium's availability,
real layout execution and the browser exit code are independent CI gates—not
a claim that the production site has been tested by the owner.

Local reproduction (development/test data only):

```sh
python -m pip install -r requirements.txt -r requirements-browser.txt
python -m playwright install --with-deps chromium
DEBUG=1 SECRET_KEY=local-browser-fixture DJANGO_ALLOW_ASYNC_UNSAFE=true \
  WEALTHARR_BROWSER_TESTS=1 \
  python manage.py test browser_tests tracker.test_browser_e2e --verbosity 2
```

`DJANGO_ALLOW_ASYNC_UNSAFE` is *only* for synchronous Playwright test code
running in an event-loop thread. **Never set it for Gunicorn, production,
scheduler, worker or actual money recording.**

## C. Non-negotiable private owner checks

The precise broker export, original workbook comparison columns, instrument
buy permissions, interactive production browser review and restore proof
remain enumerated **once** in [OWNER_TODO.md](OWNER_TODO.md). These are
not fabricated for CI and are not coding blockers, but they limit what
"Wealtharr verified" means.

At the actual private hostname, from a VPN-authorised client:

1. Verify valid TLS certificate and a forbidden result from an unauthorised
   IP/VPN state. Test the same rules against the exact proxy/TLS topology.
   Health endpoints are **not proof** of account isolation.
2. Sign in normally; choose whether to enable TOTP; confirm that an existing
   enrolled owner's password-only session cannot access Today, ledger, CSV
   import, Django admin or a direct rebalance POST before the code challenge.
   Document break-glass console recovery without a public bypass.
3. Review at 360px on a real phone and desktop keyboard for button focus,
   disclosure navigation, action copy, overflow, and actual file upload. CI
   helps but does **not** validate every physical mobile keyboard.
4. Import only after previewing and reconciling the broker CSV. Export the
   Wealtharr ledger for side-by-side comparison; never proceed on unsupported
   FX, split, fee, cash, date or ISIN assumptions.
5. Compare 9Sig worksheet outputs and GBP market source identity before
   considering any review reliable; confirm actual ISA trading access in the
   owner's broker. Avoid treating LQQ3 as mechanically equal to TQQQ.
6. Verify notifications with explicitly opted-in destinations and an observed
   scheduler heartbeat. Avoid duplicate jobs and never test with a real order.
7. Perform an independently retained **off-host encrypted** archive and
   isolated full restore using the actual backup, preserving the stable Django
   secret key for encrypted account integrations and optional TOTP.

## D. Safe release / rollback contract

Before deploying or merging into a live checkout, label the Git commit and
collect a verified PostgreSQL archive and encrypted copy of the stable
Django key. Never run `docker compose down -v`, recreate database
volumes or rotate keys to simplify a deployment. Use the private procedures
in [private-deployment-recovery.md](private-deployment-recovery.md).

Roll back application code by checking out the prior version **only if its
migrations remain forward-compatible**. Restoring a database is a separate,
destructive, owner-confirmed operation and can discard actual transactions
since the archive date; use `scripts/verify_backup.sh` before any
`scripts/restore_backup.sh` command. Failure of an auth/quote/import gate
should default to **cannot compute / no trade instruction**, not relaxed checks.

## E. Acceptance status (do not infer production GO)

| Area | What source/tests can establish | What remains unverified |
|---|---|---|
| Application engine | Deterministic synthetic 9Sig/Decimal/ledger regression suite | Owner's independently calculated 6-quarter workbook parity |
| Broker account | Fixed researched ISIN mapping; CSV parser refusal behaviour | Owner's specific ISA dealing permission and actual ledger reconciliation |
| Browser | Chromium synthetic 360px/desktop/keyboard flows once green | Real device/hostname test and production auth/proxy interactions |
| Ops/security | Production configuration and temporary synthetic PostgreSQL recovery | Live Oracle/TLS/VPN test, stable secrets, actual off-host recovery proof |
| Notifications | Preference/cancellation/dedup tests | Real SMTP/Discord delivery and observed single scheduler |
| Real-money action | Only manually entered true fills; incomplete market data blocks | **Not independently certified** as reliable real-money advice |

**No public launch or real-money execution GO is implied by this PR.** Even
after a green merge, Wealtharr may be suitable as a private tracking-only
candidate while the owner evidence remains outstanding.
