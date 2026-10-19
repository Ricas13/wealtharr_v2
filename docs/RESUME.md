# RESUME HERE — Wealtharr personal 9Sig cockpit

Repository: `Ricas13/wealtharr_v2`  
Working branch: `feature/owner-9sig-cockpit-resumable`  
Pull request: https://github.com/Ricas13/wealtharr_v2/pull/11  
Base: `main` (at start of work: `628a22f2b08930c36549bcaf4c61e4584dee1949`)  

## IMPORTANT: LETTERED PRODUCT REDESIGN — resume checkpoint 2026-10-19

**Current milestone: Sections A, B and C have been implemented as scoped branch deliverables. The next section is D only when the owner says “next”.** Existing cockpit runtime still uses the relocated v1 financial primitives; the new typed evaluation and independent reference-provider interface are available for later orchestration without connecting to any broker.

- **Same active branch:** `feature/owner-9sig-cockpit-resumable` (PR **#11**). No competing branch, no restart. Before edits, fetch current PR HEAD and check concurrent commits.
- **Section B artefacts:** `docs/instrument-research.md`, `tracker/instrument_mapping.py` (immutable `CURATED_ISA_ROLE_MAP`, `MAPPING_VERSION=2026-10-19.1`, reviewed issuer/Invest evidence with explicit pending owner ISA permission and Yahoo feed flags), `tracker/test_instrument_catalogue.py`, updated `CLAUDE.md`, `docs/architecture.md` and superseded old broker-API proposal.
- **Reviewed candidates:** `growth` = WisdomTree **LQQ3.L**, **IE00BLRPRL42**, LSE raw **GBX** (divide 100 to GBP); `safe` = iShares **ERNS.L**, **IE00BCRY6441**, LSE raw **GBP**. Both issuers show ISA eligibility and Trading 212 lists them in Invest. **The user's specific Stocks ISA buying permission and live Yahoo quote units were NOT independently confirmed.** No assertion of full ISA/broker approval is permitted.
- **Alternatives docs-only, NOT auto substitutions:** Leverage Shares `3QQQ` (**XS2472197065**) separate product; WisdomTree USD `QQQ3.L` same ISIN/different line; accumulating iShares `ERAN` (**IE000V5JWFM0**, 2026-only history); Amundi GBP-hedged `CSH2` (**LU1230136894**, broker availability unconfirmed).
- **Compatibility:** old `resolve_instruments` and GIA legacy profile remain for existing implementation/tests. New `resolve_curated_isa_role()` **only accepts UK / TRADING212 / ISA**, exact role and mapping version. Section F removes old GIA onboarding. No strategy versioned-instance migration has been performed yet; that remains forthcoming architecture implementation.
- **Strict product rule:** absolutely NO broker link/API (including read-only), no automated trading, no runtime instrument substitution, no editable strategies, no fabricated prices/fills. `docs/TRADING212_READONLY_API.md` is historical/superseded.
- **Section B validations:** evidence links reviewed 2026-10-19; regression tests added but **GitHub CI was still queued** on latest research commits at last observation. Not a claim of green tests or personal eligibility. Latest previously observed queued runs: #289/#290 at commit `e4ae963`, 2026-10-19 16:25 UTC; check actual latest on next turn.
- **Financial holds:** last completed-quarter historical prices and real ledger replay only, correct GBP/GBX scaling, exact UK instruments ≠ TQQQ/AGG, 30 Down/SPY and provisional TQQQ spike require worksheet parity, no confirmed reset baseline without real post-fill evidence. No live production deployment assumed.

### Paste this in a NEW ChatGPT chat to resume

> Continue Wealtharr v2 on PR #11, same branch `feature/owner-9sig-cockpit-resumable`. Read `docs/RESUME.md` and Section C implementation. Sections A (architecture), B (fixed issuer evidence-aware UK ISA mappings), C (pure pinned 9Sig v1 evaluator, audit, owner parity placeholders and independent USD reference fallback) are implemented. Do ONLY **Section D**: strategy-independent audited metrics and exact flow-boundary valuation semantics. Do not revisit or harden the old cockpit, and do not re-check prior safety work. The owner-only inputs are already itemized once in `docs/OWNER_TODO.md` and do not block Section D. Commit to this same branch; update this handoff once at the end; stop and await “next”.
## Objective

Make Wealtharr a strategy-agnostic **tracking and notification** platform for a private owner, with fixed 9Sig as its first (and only currently implemented) strategy. Today shows performance and one next action; the owner manually makes and confirms any broker trades. The product never places orders, never connects to brokers (including read-only APIs), and never invents prices, trades or backtests. Preserve the append-only ledger, immutable confirmed reset baselines and financial refusal rules throughout the refactor.

## New product brief — lettered redesign (2026-10-19)

- [x] **Section A architecture/documentation only:** committed `docs/architecture.md` and root `CLAUDE.md` to this existing branch. These describe a *target*, not running refactored code; current PR #11 cockpit and its tests are still in place.
- [x] **B** research issuer identities and issuer-reported ISA status, Trading 212 *Invest* listings, explicit unverified actual Stocks ISA access, alternatives and frozen evidence-aware mapping; see `docs/instrument-research.md`. Account permission and Yahoo live metadata remain unverified.
- [x] **C** pure pinned `tracker/strategies/nine_sig/v1.py` engine (v1.0.0), immutable inputs/results and audit trace, irregular dated cashflows, 30 Down/100%-up warning, quote refusal; independent, USD-reference-only Stooq fallback in `tracker/core/prices.py`, plus synthetic unit coverage and deliberately empty six-quarter owner parity fixtures.
- [ ] **D** audited universal metrics with exact flow-boundary tests.
- [ ] **E** strategy-independent Today / History / Ledger / Strategy info presentation.
- [ ] **F** one-screen onboarding, contributions and conservative T212 import.
- [ ] **G** safe single-transaction owner-confirmed fills and actual-baseline handling.
- [ ] **H** owner destination/alert matrix, idempotent digest, scheduler diagnostics.
- [ ] **I** secure Traefik deployment, optional TOTP, backups/restore.
- [ ] **J** CI/Playwright and incremental ship plan.
- **Instruction:** user wants **one lettered section per turn**. Stop after C now; proceed with D **only** when user says "next". Preserve branch, commit history, PR and safety until tested replacements exist.
- **Architecture decision:** no broker/API integration of any kind, no editable preset/ticker/rule UI, no paper account, no billing or public signup. Fixed code-defined strategy versions; role-based evidence-backed exact lines pinned at enrolment. See `docs/architecture.md` and `CLAUDE.md`.
- **Outstanding verification remains outstanding:** workbook parity, ISA/broker eligibility, real CSV, CI/migrations/browser checks, and deployment. Section A did not test or deploy runtime changes.

## Section C delivery — 2026-10-19

- [x] Fixed v1 Python rules extracted from `tracker/market_signals.py` to `tracker/strategies/nine_sig/v1.py`; existing Django adapter delegates pure quarterly calculations without starting a second strategy implementation.
- [x] Typed `PortfolioHistory`, `NormalizedQuote`, `ApprovedPriceBundle`, immutable rule parameters and `EvaluationResult`; quarter-by-quarter 9% growth and half-of-actual-irregular-contributions audit, reserve/throttle intermediates, 30 Down state, non-tradable TQQQ 100%-up proxy warning, and defined cannot-compute result.
- [x] Strict GBP/GBX price-unit/source/date validation and pinned instrument/strategy identity. An optional independent **Stooq USD TQQQ/SPY reference** adapter requires actual overlap with an independently identified Yahoo observation; never substitutes UK execution prices and never silently persists fallback data. The adapter is a new-source interface, not a guarantee that any independent feed is available.
- [x] Six intentionally empty case rows in `tracker/strategies/nine_sig/fixtures/workbook_parity.csv`; separate quote and external-flow fixtures; automated test compares genuine case data when filled, otherwise explicitly skips only the owner-parity test. Synthetic engine/provider tests added but new CI result not asserted.
- [x] All five required owner-supplied input types, including exact worksheet columns, dates and fixture formats, live in `docs/OWNER_TODO.md` **once**; not blockers for the next build section.
- [ ] Section **D** next. No automatic broker trades, no broker integration, no extra cockpit-hardening work in this milestone.

## Section C follow-through — price quality, audit and CI (2026-10-19)

**Scoped source changes on existing PR #11**:
- `tracker/strategies/nine_sig/v1.py`: exact selected line guard (`LQQ3.L` / `ERNS.L`), required TQQQ and SPY closes within six days of each historical quarter end, explicit reset-reason and proposed-quantity audit values; unchanged 9%/half-flow, throttle and 30 Down arithmetic.
- `tracker/core/prices.py`: pure `check_price_series()` returning **APPROVED** or **HOLD** for stale/future/duplicate/nonpositive or anomalous normalized vendor observations. Thresholds are conservative screening thresholds, **not** automatic re-pricing or corporate-action adjustments.
- `tracker/market_signals.py`: fail closed for Yahoo LQQ3 GBX/ERNS GBP/SPY-TQQQ USD metadata mismatches; persist `YahooSync.last_error=PRICE_HOLD:<code>` and refuse new reviews while a required series is on hold. Never silently rewrite an accepted `MarketObservation` when Yahoo revises an earlier historical close; review first. The legacy Django scheduling adapter still persists old-style signal rows.
- `tracker/test_section_c_quality.py`: regressions for stale/huge/future/bad quotes, exact pinned lines, early historical quarter lookback, revision holds, and no DB write after rejected data. `tracker/test_nine_sig_audit.py` corrected reason-code assertions.
- `tracker/test_paper_trial.py` regression now correctly expects **no invented valuation snapshot** from a hand-edited `PriceMark`; unrelated paper-trading implementation remains slated for later removal.
- `docs/FINANCIAL_QA.md` obsolete read-only broker API allowance removed: **all broker links forbidden**.

**Verified CI:** workflow run [#366](https://github.com/Ricas13/wealtharr_v2/actions/runs/39289526373) succeeded at commit `10078b8167d43c4da47e0f7225f40a76a0a3eff3`: Django tests (except intentionally skipped owner parity), model/migrations check, migration application, collectstatic, public production smoke, Compose/restore-helper syntax and non-gating Yahoo smoke. Later docs or concurrent Section D commits each trigger new checks; do not assume those passed until checked.

**Remaining specific C boundaries (no fabricated assurance):**
1. **Real six-quarter workbook comparison remains open**; the parity harness skips missing owner fixture fields, and should not be labelled financially verified. `docs/OWNER_TODO.md` lists required real inputs once.
2. **Owner ISA permission and live Yahoo raw GBP/GBX metadata** remain unverified. Existing recorded historical market observations did not retain raw quote metadata; the typed `NormalizedQuote` adapter/quality gate needs source provenance when the future versioned core orchestration is activated.
3. **Stooq fallback is a separately testable, corroborated USD reference-only adapter**, not a deployed universal fallback for UK trading lines. The existing scheduler still relies on verified Yahoo/DB prices; a missing UK line correctly refuses rather than substituting Stooq. Never treat two Yahoo hosts as independent providers.
4. **The eventual immutable `PriceDecision`/audit model and generic core orchestrator** described in `docs/architecture.md` have not been migrated into the current DB schema. Current YahooSync hold is an interim persisted safety signal; no manual hold-clear UI exists yet. Do not mark such plumbing complete.
5. The TQQQ 100%-up comparison is a provisional calendar-close warning, **not** the owner's executed rebalance reference; never issue GBP trade orders from it.

**Next scope:** Section **D only** when the owner says "next"; retain this C evidence and do not redo it. Refresh live PR HEAD before editing because another continuation may add changes on the same branch.

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

1. Continue **PR #11** on `feature/owner-9sig-cockpit-resumable`; inspect current files to avoid overwriting concurrent work.
2. Read this handoff and `docs/architecture.md`; Section C files: `tracker/strategies/nine_sig/v1.py`, `tracker/core/prices.py`, `tracker/test_nine_sig_v1.py` and the workbook fixture directory.
3. Implement **Section D only**. Use `docs/OWNER_TODO.md` for owner evidence without restating or blocking on it.
4. Leave one current-branch PR and update the handoff at the end; never claim queued checks passed.

## Critical financial semantics

- Do not equate LQQ3/ERNS with TQQQ/AGG; instruments have different tracking, resets, fees and FX.
- Actual executions require owner confirmation; ordinary quarterly trades never generate a new 60/40 reset baseline.
- New 60/40 baseline needs actual fills, verified later close, GBP instruments, >=0 cash and equity within 5pp allocation.
- Trading 212 CSV import is intentionally conservative and atomic: preview rejects unmapped/FX/uncertain rows.
- XIRR is money-weighted, not time-weighted. Exact TWR/drawdown withheld absent flow-boundary unitisation.
- The spike reference and discretionary 9Sig exceptions still need comparison to owner's workbook. Do not label the rules “verified” until independently confirmed.

## User prompt for a new chat

**Use the precise Section D-only pasteable prompt at the top of this file.** Keep the user's letter-by-letter stop condition. Later progress may update the next section; never infer sections complete from queued CI.
