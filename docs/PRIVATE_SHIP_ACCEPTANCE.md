# Wealtharr v2 — owner-only private ship acceptance (Section J)

Branch: `feature/owner-9sig-cockpit-resumable` · [PR #11](https://github.com/Ricas13/wealtharr_v2/pull/11) · 19 October 2026

**Acceptance is evidence-based.** A committed screen, passing synthetic test or queued workflow is *not* evidence of real owner investments, instrument access, mailbox delivery or restored Oracle data. This is a private single-owner tracking tool, **not** a live broker, automated dealing system or customer SaaS launch. Only manual owner-confirmed investments enter the ledger.

## Automated acceptance matrix

| Capability | Automated evidence / command | Meaning of a green result |
| --- | --- | --- |
| Django models, migrations, portfolio maths and owner permissions | `python manage.py test tracker`, `python manage.py makemigrations --check --dry-run` | Synthetic server logic only; not reconciled to real brokerage |
| True browser login and private-page isolation | `python manage.py test browser_tests` in Playwright CI Chromium | Unauthenticated pages redirect; other owner's portfolio/rebalance view cannot be read |
| Owner journey and readable responsive screens | Browser suite at **360px** and **1280px** | Today, History, Ledger, Strategy and Settings render, CSS loads, no full-document overflow or JS page exceptions |
| Keyboard accessibility | Browser suite keyboard skip-link, mobile navigation and Escape | Usable keyboard route through real navigation; does not claim full WCAG audit |
| Safe no-data outcome | Browser suite with actual recorded positions but **no quote observations** | Verified value is **Unavailable**; no executable rebalance action |
| Actual deposit and dated manual partial trade | Browser suite validates form POST then saved ledger/receipt | A real-form cash deposit is recorded once; partial sell does not create a finished rebalance or 60/40 baseline |
| Fixed UK ISA onboarding | Browser suite chooses cash or no opening cash | £500 plan is not a deposit; optional owner-asserted starting cash generates exactly one dated entry |
| TOTP gate | Browser suite enables an encrypted fixture factor | Private ledger remains inaccessible until the owner submits a valid one-time code |
| Production build and restore helper | Application CI production static smoke, Compose config and **synthetic** PostgreSQL isolated full-restore step | Deployment configuration parses and a synthetic archive restored; not a live restore rehearsal |
| Browser/build dependency separation | `browser_tests/` standalone; Playwright pinned and installed **only inside CI browser job** | Production Django and scheduler remain server-rendered without Playwright/Node build dependencies |

Browser CI launches headless Chromium with **generated test-only accounts and transactions**. The test job's `DJANGO_ALLOW_ASYNC_UNSAFE` flag exists **only** to accommodate Playwright's synchronous event loop in the isolated disposable Django test process; it must never appear in production Compose, Gunicorn or reminders.

On a CI failure, inspect that job's actual error, repair code/tests and rerun the branch workflow. Report the **exact run number and SHA**. Do not present an earlier green run as confirmation of a newer commit.

## Owner-only release sequence (small, reversible changes)

1. **Code gate:** Review PR #11 diff, confirm every required workflow job is green for the actual merge commit (including Chromium mobile), no destructive migration, no new broker connector or auto-trading permissions. Merge the **existing** PR only after this acceptance; do not create another release PR to restart the cycle.
2. **Protect state first:** Identify the correct private Oracle deployment, its data volume and the stable existing encryption key. Capture and independently store a real PostgreSQL archive **plus checksum**, and perform a full restore drill on a disposable host before replacing containers or changing configuration. Do not change `SECRET_KEY` casually.
3. **Deploy privately:** Follow `docs/private-deployment-recovery.md` for the correct Traefik setup (host loopback or approved Docker network), pinned hostname, HTTPS, trusted proxy headers, credentials, file permissions and Compose health. Deployment commands are **not** issued automatically by GitHub CI.
4. **Owner journey before reliance:** Sign in on a real phone-sized and desktop browser. Confirm normal Today / History / Ledger / Strategy / Settings navigation, the optional authenticator challenge, clear unavailable-data messages, owner-confirmed deposits/import review, and manual fill progress. Reconcile balances with your own financial records; do not treat a synthetic expected result as real parity.
5. **Observe, do not assume:** Confirm hourly scheduler diagnostics, real opt-in SMTP/Discord receipt, backups continuing on the host, independent encrypted off-host retention, restore capability and health from the owner environment. Note whether data is verified before acting on any instruction.
6. **Incremental private use:** Start with view-only confidence and historical reconciliation, then real contributions and *owner-executed* fill receipts. Evaluate whether each quarterly review requires fewer interactions than the original spreadsheet; never enable automatic dealing to shortcut the journey.

Original owner-provided evidence requirements remain **only** in `docs/OWNER_TODO.md`. Do not copy them into another checklist or claim they were independently fulfilled here.

## Release verdict rules

**Source acceptance:** MAY be marked **PASS** only when Django tests, migration checks, production-mode smoke, and all required headless Chromium journeys are green against the final commit. A green synthetic restore proves the helper's test architecture, not the protection of real Oracle data.

**Owner private deployment acceptance:** stays **NOT VERIFIED** until the owner demonstrates working TLS/Traefik privacy, their own authentication/TOTP path (if enabled), persisted and correctly restored real PostgreSQL history, off-host backups, and actual notification delivery. An inaccessible integration must be reported as unverified, never silently waived.

**Investment-strategy fidelity:** independent workbook and actual broker evidence require the exact owner input already tracked separately. The app's date-aware no-price refusal is intentional, not an excuse to fabricate historical comparisons.

**Do not promise:** a live Oracle deployment, signed-off ISA brokerage access, exact 9Sig workbook parity, or an available real 9Sig quarterly signal merely from CI success.

## Recovery and rollback

A failed release should first stop the application/scheduler **without deleting volumes**, preserve current evidence, and revert the application image/checkout while keeping the existing database compatible. If a database restore is genuinely required, use the separately verified archived copy with the explicit destructive guard in `scripts/restore_backup.sh`. Restoring changes financial history; identify and explicitly accept the time window of entries that would be lost. Never run `docker compose down -v` as a rollback.

## Final checkpoint

The lettered redesign (A–J) is a **source implementation milestone**, not an implicit production approval. The next action after the completed PR is owner acceptance and manual private deployment, **not** another audit/hardening cycle or automatic financial trades. Compare actual outcomes side-by-side with the original spreadsheet only using owner-provided verified observations.
