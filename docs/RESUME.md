# RESUME HERE — Wealtharr personal 9Sig cockpit

Repository: `Ricas13/wealtharr_v2`  
Working branch: `feature/owner-9sig-cockpit-resumable`  
Pull request: https://github.com/Ricas13/wealtharr_v2/pull/11  
Base: `main` (at start of work: `628a22f2b08930c36549bcaf4c61e4584dee1949`)  

## IMPORTANT: LETTERED PRODUCT REDESIGN — resume checkpoint 2026-10-19

**Current milestone: Sections A and B completed as scoped deliverables; do not infer Sections C–J are implemented. The user's instruction is ONE SECTION PER CHAT TURN, wait for "next".** The next section is **C**, not a restart of the old cockpit and not an authorisation to do all future sections. Section A documented the target architecture; Section B researched UK Trading 212 ISA instruments and committed the evidence-aware catalogue.

- **Same active branch:** `feature/owner-9sig-cockpit-resumable` (PR **#11**). No competing branch, no restart. Before edits, fetch current PR HEAD and check concurrent commits.
- **Section B artefacts:** `docs/instrument-research.md`, `tracker/instrument_mapping.py` (immutable `CURATED_ISA_ROLE_MAP`, `MAPPING_VERSION=2026-10-19.1`, reviewed issuer/Invest evidence with explicit pending owner ISA permission and Yahoo feed flags), `tracker/test_instrument_catalogue.py`, updated `CLAUDE.md`, `docs/architecture.md` and superseded old broker-API proposal.
- **Reviewed candidates:** `growth` = WisdomTree **LQQ3.L**, **IE00BLRPRL42**, LSE raw **GBX** (divide 100 to GBP); `safe` = iShares **ERNS.L**, **IE00BCRY6441**, LSE raw **GBP**. Both issuers show ISA eligibility and Trading 212 lists them in Invest. **The user's specific Stocks ISA buying permission and live Yahoo quote units were NOT independently confirmed.** No assertion of full ISA/broker approval is permitted.
- **Alternatives docs-only, NOT auto substitutions:** Leverage Shares `3QQQ` (**XS2472197065**) separate product; WisdomTree USD `QQQ3.L` same ISIN/different line; accumulating iShares `ERAN` (**IE000V5JWFM0**, 2026-only history); Amundi GBP-hedged `CSH2` (**LU1230136894**, broker availability unconfirmed).
- **Compatibility:** old `resolve_instruments` and GIA legacy profile remain for existing implementation/tests. New `resolve_curated_isa_role()` **only accepts UK / TRADING212 / ISA**, exact role and mapping version. Section F removes old GIA onboarding. No strategy versioned-instance migration has been performed yet; that remains forthcoming architecture implementation.
- **Strict product rule:** absolutely NO broker link/API (including read-only), no automated trading, no runtime instrument substitution, no editable strategies, no fabricated prices/fills. `docs/TRADING212_READONLY_API.md` is historical/superseded.
- **Section B validations:** evidence links reviewed 2026-10-19; regression tests added but **GitHub CI was still queued** on latest research commits at last observation. Not a claim of green tests or personal eligibility. Latest previously observed queued runs: #289/#290 at commit `e4ae963`, 2026-10-19 16:25 UTC; check actual latest on next turn.
- **Financial holds:** last completed-quarter historical prices and real ledger replay only, correct GBP/GBX scaling, exact UK instruments ≠ TQQQ/AGG, 30 Down/SPY and provisional TQQQ spike require worksheet parity, no confirmed reset baseline without real post-fill evidence. No live production deployment assumed.

### Paste this in a NEW ChatGPT chat to resume

> Continue Wealtharr v2 at https://github.com/Ricas13/wealtharr_v2, existing **PR #11** and branch `feature/owner-9sig-cockpit-resumable`. **Read `docs/RESUME.md` first**, then `CLAUDE.md`, `docs/architecture.md`, `docs/instrument-research.md` and `docs/FINANCIAL_QA.md`. Sections **A (architecture/docs) and B (issuer/Invest research + versioned UK ISA role catalogue)** have been committed. Implement **ONLY Section C** of the lettered redesign: extract a pure version-pinned 9Sig evaluation module from `tracker/market_signals.py`, with audit intermediates, irregular contributions, 30 Down/100%-up warning and worksheet parity fixtures; add an independent price fallback and fail-closed sanity/quote-unit checks. Preserve all existing financial safeguards and tests; inspect live PR CI, fix regressions; commit to the **same branch** and update `docs/RESUME.md` with exact completed/remaining status. Do **not** ask me to re-explain the brief, start over, create another branch, connect Trading 212 or place trades. Stop at the end of Section C and wait for my "next".

## Objective

Make Wealtharr a strategy-agnostic **tracking and notification** platform for a private owner, with fixed 9Sig as its first (and only currently implemented) strategy. Today shows performance and one next action; the owner manually makes and confirms any broker trades. The product never places orders, never connects to brokers (including read-only APIs), and never invents prices, trades or backtests. Preserve the append-only ledger, immutable confirmed reset baselines and financial refusal rules throughout the refactor.

## New product brief — lettered redesign (2026-10-19)

- [x] **Section A architecture/documentation only:** committed `docs/architecture.md` and root `CLAUDE.md` to this existing branch. These describe a *target*, not running refactored code; current PR #11 cockpit and its tests are still in place.
- [x] **B** research issuer identities and issuer-reported ISA status, Trading 212 *Invest* listings, explicit unverified actual Stocks ISA access, alternatives and frozen evidence-aware mapping; see `docs/instrument-research.md`. Account permission and Yahoo live metadata remain unverified.
- [ ] **C** pure version-pinned 9Sig module, spreadsheet parity/irregular contributions, quote providers/quality holds.
- [ ] **D** audited universal metrics with exact flow-boundary tests.
- [ ] **E** strategy-independent Today / History / Ledger / Strategy info presentation.
- [ ] **F** one-screen onboarding, contributions and conservative T212 import.
- [ ] **G** safe single-transaction owner-confirmed fills and actual-baseline handling.
- [ ] **H** owner destination/alert matrix, idempotent digest, scheduler diagnostics.
- [ ] **I** secure Traefik deployment, optional TOTP, backups/restore.
- [ ] **J** CI/Playwright and incremental ship plan.
- **Instruction:** user wants **one lettered section per turn**. Stop after B now; proceed with C **only** when user says "next". Preserve branch, commit history, PR and safety until tested replacements exist.
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
- [x] Historical Trading 212 read-only API note explicitly superseded by current **NO broker API/link** product rule.
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

1. Open PR #11 and the **same** `feature/owner-9sig-cockpit-resumable` branch; inspect the newest commit/CI and reconcile any concurrent writes.
2. Read `CLAUDE.md`, `docs/architecture.md`, `docs/instrument-research.md`, this file and `docs/FINANCIAL_QA.md`. Do not ask for the product brief again.
3. Use the **Section C-only prompt at the top** once the user says "next". Do not jump to D–J. All new edits belong on the same branch/PR, without rewriting already-committed work.
4. Run/check CI including `DEBUG=1 SECRET_KEY=test-only python manage.py check`, `python manage.py makemigrations --check --dry-run`, `python manage.py test tracker`, and `python manage.py collectstatic --noinput`. If a Section B regression is found, fix it while advancing the authorised section.
5. Update this checkpoint with genuine results (no "green" for queued CI, no "ISA verified" for public Invest listings), note remaining work and leave a pasteable next-section prompt.
6. Do not deploy or alter infrastructure without separate explicit authorisation; keep one scheduler replica and never run both competing notifier commands.

## Critical financial semantics

- Do not equate LQQ3/ERNS with TQQQ/AGG; instruments have different tracking, resets, fees and FX.
- Actual executions require owner confirmation; ordinary quarterly trades never generate a new 60/40 reset baseline.
- New 60/40 baseline needs actual fills, verified later close, GBP instruments, >=0 cash and equity within 5pp allocation.
- Trading 212 CSV import is intentionally conservative and atomic: preview rejects unmapped/FX/uncertain rows.
- XIRR is money-weighted, not time-weighted. Exact TWR/drawdown withheld absent flow-boundary unitisation.
- The spike reference and discretionary 9Sig exceptions still need comparison to owner's workbook. Do not label the rules “verified” until independently confirmed.

## User prompt for a new chat

**Use the precise Section C-only pasteable prompt at the top of this file.** Keep the user's letter-by-letter stop condition. Later progress may update the next section; never infer sections complete from queued CI.
