# RESUME HERE — Wealtharr personal 9Sig cockpit

Repository: `Ricas13/wealtharr_v2`  
Working branch: `feature/owner-9sig-cockpit-resumable`  
Pull request: https://github.com/Ricas13/wealtharr_v2/pull/11  
Base: `main` (at start of work: `628a22f2b08930c36549bcaf4c61e4584dee1949`)  

## Objective

Make Wealtharr a single-owner 9Sig investment cockpit: Today (value, contributed, gains, a reliable next action), History, Ledger, Settings, one guided manual rebalance, Trading 212 CSV preview/import, one-screen setup, money-weighted returns, concise reminders. Do not place broker orders or invent prices. Existing append-only ledger, immutable reset baseline, and hard refusals must remain intact.

## New product brief — lettered redesign (2026-10-19)

- [x] **Section A architecture/documentation only:** committed `docs/architecture.md` and root `CLAUDE.md` to this existing branch. These describe a *target*, not running refactored code; current PR #11 cockpit and its tests are still in place.
- [ ] **B** independently verify exact Trading 212 UK ISA instrument mapping, evidence/dated flags and alternatives; do not treat current LQQ3/ERNS mapping as definitively ISA-verified.
- [ ] **C** pure version-pinned 9Sig module, spreadsheet parity/irregular contributions, quote providers/quality holds.
- [ ] **D** audited universal metrics with exact flow-boundary tests.
- [ ] **E** strategy-independent Today / History / Ledger / Strategy info presentation.
- [ ] **F** one-screen onboarding, contributions and conservative T212 import.
- [ ] **G** safe single-transaction owner-confirmed fills and actual-baseline handling.
- [ ] **H** owner destination/alert matrix, idempotent digest, scheduler diagnostics.
- [ ] **I** secure Traefik deployment, optional TOTP, backups/restore.
- [ ] **J** CI/Playwright and incremental ship plan.
- **Instruction:** user wants **one lettered section per turn**. Stop after A now; proceed with B **only** when user says "next". Preserve branch, commit history, PR and safety until tested replacements exist.
- **Architecture decision:** no broker/API integration of any kind, no editable preset/ticker/rule UI, no paper account, no billing or public signup. Fixed code-defined strategy versions; role-based evidence-backed exact lines pinned at enrolment. See `docs/architecture.md` and `CLAUDE.md`.
- **Outstanding verification remains outstanding:** workbook parity, ISA/broker eligibility, real CSV, CI/migrations/browser checks, and deployment. Section A did not test or deploy runtime changes.

## State / checkpoints

- [x] Dedicated new branch created; `main` deliberately untouched.
- [x] Added all Django cockpit modules, templates, CSS and JS from the reviewed reference patch.
- [x] Added additive `RebalanceExecution` and `BrokerImportRow` models and migration `0009`.
- [x] Wired `/cockpit/`, set Today as authenticated landing, simplified navigation, preserved legacy URLs.
- [x] Prevented duplicate immediate quarterly notification delivery; one concise digest scheduler replaces legacy monthly sender.
- [x] Source Python compile/syntax checks and standalone Decimal examples in authoring environment.
- [x] CSV event-ID collision protection and mixed-timezone parsing regression coverage.
- [x] Optional Trading 212 read-only API security/rollout decision note.
- [ ] GitHub Actions was **queued** at the last check; GitHub CI: `python manage.py check`, `makemigrations --check --dry-run`, migrate, tests, collectstatic. **Inspect live CI results and fix until green.**
- [ ] Run dry-run CSV preview with a REAL, redacted Trading 212 export; ensure currency, fees, splits/dividends and ISIN mapping match broker records.
- [ ] Validate every quarter against owner's Jason Kelly workbook (see `docs/FINANCIAL_QA.md`); current signal engine documents unverified discretionary branches.
- [ ] Manual in-browser mobile/accessibility smoke and notifications test via configured destination.
- [ ] Backup/restore verification then deploy to Oracle only with authorized infrastructure access. DO NOT assume this branch is live.

## Integration and financial-safety continuation — 2026-10-19

**Work committed on the SAME branch and PR #11** (independent of the architecture-only section A notes above):

- [x] Disallow stale quarterly rebalance URLs on both GET and locked POST; refuse revoked signals after rechecking their state. Refuse negative holdings and disabled/non-GBP monitoring for executable instructions.
- [x] Suppress exact XIRR/cumulative returns where held sleeve prices are from different quote dates. A one-day gap is not an exact portfolio liquidation valuation.
- [x] Trading 212 CSV import requires a successful session-bound preview of identical file bytes, re-upload on confirmation, and revalidation under the locked strategy transaction. Manual overlap, changed file, conflicting broker IDs and unsupported/unverifiable ISIN are refused; exact repeats remain idempotent.
- [x] Snapshot scheduler requires same-day GBP MarketObservation-backed prices, not two asynchronous Yahoo marks. Regression tests exercise rejection of mismatched dates and currency.
- [x] Ordinary 9Sig fill confirmation now also checks the safe-asset sleeve against the proposed quantities within 5 portfolio percentage points; deviations roll back both fills and execution marker.
- [x] Exact account/broker instrument mapping is required for actionable instructions. Legacy unknown-broker/ticker combinations cannot reach fill confirmation even when a GBP quote exists. End-to-end digest fixture uses the supported Trading 212 account profile.
- [x] Replaced the two stale immediate-notification assertions with checks for urgent 30 Down dispatch and the deferred quarterly digest. Snapshot fixtures now include genuine matching MarketObservation records.
- [x] Stop follow-up quarterly trade reminders once newer ledger entries invalidate their quantities; keep independent monthly contribution reminders. Added a regression test that suppresses obsolete quarterly notices.
- [ ] **CI must finish green on current HEAD.** Latest newly-triggered run observed: GitHub Actions #282 (`39285007151`), queued at 2026-10-19 16:23 UTC; its result is not yet known.  First original PR run `39281003314` (`7969441`) passed system check, no-pending-migrations, and migration, but 135 tests produced two failures: `tracker.test_market_signals.MarketWorkflowTests.test_review_creates_and_deduplicates_persistent_notifications` and `tracker.test_real_9sig_signals.RealQuarterlySignalTests.test_later_same_quarter_deposit_does_not_inflate_original_equity_basis`. Both asserted legacy immediate quarterly dispatch, inconsistent with the replacement digest. Their assertions are now updated but *not yet proved green* by new CI.
- [ ] Check latest branch run and its job logs after it leaves the queue. Fix any new failures, then verify `collectstatic`, production-mode smoke, Compose and restore helper; do not infer success from a queued run.
- [ ] Preview a **real, redacted** Trading 212 CSV; trade rows now require matching ISIN. Reconcile totals/fees, split handling, corporate actions, exact instrument line and every position. Unknown actions intentionally refuse import.
- [ ] Six-quarter Jason Kelly workbook parity, independent cash-flow metrics, ISA eligibility, browser/mobile accessibility, notification endpoint delivery and infrastructure recovery remain open. No real-money execution guidance is independently certified yet.

**Concurrent editing caution:** another continuation added `docs/architecture.md` and `CLAUDE.md` to this same branch. Preserve these architectural notes and reconcile live branch HEAD before each write. No alternate branch was created. Existing runtime is not automatically refactored to that future architecture.

## First commands in a NEW CHAT

1. Open the repo and branch: `https://github.com/Ricas13/wealtharr_v2/tree/feature/owner-9sig-cockpit-resumable`.
2. Read this file, `docs/INSTALL.md` and `docs/FINANCIAL_QA.md` plus latest PR and CI statuses.
3. Fix failing checks. Do not start a second competing branch unless the PR has been merged.
4. Use this branch and its PR for all further commits; update this checklist after each meaningful step. Treat unmarked checks as OPEN, not silently complete.
5. Test: `DEBUG=1 SECRET_KEY=test-only python manage.py check; python manage.py makemigrations --check --dry-run; python manage.py test tracker; python manage.py collectstatic --noinput`.
6. Follow migration and deployment instructions in `docs/INSTALL.md`. Keep one scheduler replica, do not run old and new notification commands together.

## Critical financial semantics

- Do not equate LQQ3/ERNS with TQQQ/AGG; instruments have different tracking, resets, fees and FX.
- Actual executions require owner confirmation; ordinary quarterly trades never generate a new 60/40 reset baseline.
- New 60/40 baseline needs actual fills, verified later close, GBP instruments, >=0 cash and equity within 5pp allocation.
- Trading 212 CSV import is intentionally conservative and atomic: preview rejects unmapped/FX/uncertain rows.
- XIRR is money-weighted, not time-weighted. Exact TWR/drawdown withheld absent flow-boundary unitisation.
- The spike reference and discretionary 9Sig exceptions still need comparison to owner's workbook. Do not label the rules “verified” until independently confirmed.

## User prompt for a new chat

> Continue `Ricas13/wealtharr_v2` on branch `feature/owner-9sig-cockpit-resumable` using `docs/RESUME.md`. Read the PR and CI, fix every outstanding test/migration/flow defect, verify the 9Sig safety checklist, and update the resume file. Do not start over or replace the append-only ledger. Work as far as possible in this chat and leave the branch and PR in a resumable state.
