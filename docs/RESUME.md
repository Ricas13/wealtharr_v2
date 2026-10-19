# RESUME HERE — Wealtharr personal 9Sig cockpit

Repository: `Ricas13/wealtharr_v2`  
Working branch: `feature/owner-9sig-cockpit-resumable`  
Pull request: https://github.com/Ricas13/wealtharr_v2/pull/11  
Base: `main` (at start of work: `628a22f2b08930c36549bcaf4c61e4584dee1949`)  

## IMPORTANT: LETTERED PRODUCT REDESIGN — resume checkpoint 2026-10-19

**Current milestone: Sections A–E implemented as scoped branch deliverables. The next section is F only when the owner says “next”.** Section E introduced strategy-neutral owner-page presenters, read-only released strategy explanations and simplified Today / History / Ledger presentation on this branch. Only the already-supported 9Sig strategy is selectable today; Section F handles the new one-screen onboarding and fixed product mapping.

- **Same active branch:** `feature/owner-9sig-cockpit-resumable` (PR **#11**). No competing branch, no restart. Before edits, fetch current PR HEAD and check concurrent commits.
- **Section B artefacts:** `docs/instrument-research.md`, `tracker/instrument_mapping.py` (immutable `CURATED_ISA_ROLE_MAP`, `MAPPING_VERSION=2026-10-19.1`, reviewed issuer/Invest evidence with explicit pending owner ISA permission and Yahoo feed flags), `tracker/test_instrument_catalogue.py`, updated `CLAUDE.md`, `docs/architecture.md` and superseded old broker-API proposal.
- **Reviewed candidates:** `growth` = WisdomTree **LQQ3.L**, **IE00BLRPRL42**, LSE raw **GBX** (divide 100 to GBP); `safe` = iShares **ERNS.L**, **IE00BCRY6441**, LSE raw **GBP**. Both issuers show ISA eligibility and Trading 212 lists them in Invest. **The user's specific Stocks ISA buying permission and live Yahoo quote units were NOT independently confirmed.** No assertion of full ISA/broker approval is permitted.
- **Alternatives docs-only, NOT auto substitutions:** Leverage Shares `3QQQ` (**XS2472197065**) separate product; WisdomTree USD `QQQ3.L` same ISIN/different line; accumulating iShares `ERAN` (**IE000V5JWFM0**, 2026-only history); Amundi GBP-hedged `CSH2` (**LU1230136894**, broker availability unconfirmed).
- **Compatibility:** old `resolve_instruments` and GIA legacy profile remain for existing implementation/tests. New `resolve_curated_isa_role()` **only accepts UK / TRADING212 / ISA**, exact role and mapping version. Section F removes old GIA onboarding. No strategy versioned-instance migration has been performed yet; that remains forthcoming architecture implementation.
- **Strict product rule:** absolutely NO broker link/API (including read-only), no automated trading, no runtime instrument substitution, no editable strategies, no fabricated prices/fills. `docs/TRADING212_READONLY_API.md` is historical/superseded.
- **Section B validations:** evidence links reviewed 2026-10-19; regression tests added but **GitHub CI was still queued** on latest research commits at last observation. Not a claim of green tests or personal eligibility. Latest previously observed queued runs: #289/#290 at commit `e4ae963`, 2026-10-19 16:25 UTC; check actual latest on next turn.
- **Financial holds:** last completed-quarter historical prices and real ledger replay only, correct GBP/GBX scaling, exact UK instruments ≠ TQQQ/AGG, 30 Down/SPY and provisional TQQQ spike require worksheet parity, no confirmed reset baseline without real post-fill evidence. No live production deployment assumed.

### Paste this in a NEW ChatGPT chat to resume

> Continue Wealtharr v2 from **PR #11**, the existing `feature/owner-9sig-cockpit-resumable` branch. **Read `docs/RESUME.md` first**, then `docs/architecture.md`, `tracker/core/presentation.py`, `tracker/cockpit.py` and the current templates. Sections A–E are implemented in this branch; the latest is Section E's owner-only, source-grounded Today / History / Ledger / Strategy guide, with a fixed-version read-only description, clear one-next-action states, recorded valuation explanations, historical review evidence, and cash-ledger filtering/export. Implement **ONLY Section F**: one-screen onboarding, actual owner contributions and conservative Trading 212 CSV import UX, preserving prior import integrity and using the exact UK ISA role catalogue without any broker API. Do not revisit or re-harden prior safeguards or fake fills/prices/eligibility. Work on this **same PR and branch**, commit the Section F change, update the handoff at the end, and stop until “next”.
## Objective

Make Wealtharr a strategy-agnostic **tracking and notification** platform for a private owner, with fixed 9Sig as its first (and only currently implemented) strategy. Today shows performance and one next action; the owner manually makes and confirms any broker trades. The product never places orders, never connects to brokers (including read-only APIs), and never invents prices, trades or backtests. Preserve the append-only ledger, immutable confirmed reset baselines and financial refusal rules throughout the refactor.

## New product brief — lettered redesign (2026-10-19)

- [x] **Section A architecture/documentation only:** committed `docs/architecture.md` and root `CLAUDE.md` to this existing branch. These describe a *target*, not running refactored code; current PR #11 cockpit and its tests are still in place.
- [x] **B** research issuer identities and issuer-reported ISA status, Trading 212 *Invest* listings, explicit unverified actual Stocks ISA access, alternatives and frozen evidence-aware mapping; see `docs/instrument-research.md`. Account permission and Yahoo live metadata remain unverified.
- [x] **C** pure pinned `tracker/strategies/nine_sig/v1.py` engine (v1.0.0), immutable inputs/results and audit trace, irregular dated cashflows, 30 Down/100%-up warning, quote refusal; independent, USD-reference-only Stooq fallback in `tracker/core/prices.py`, plus synthetic unit coverage and deliberately empty six-quarter owner parity fixtures.
- [x] **D** pure `tracker/core/metrics.py` v1 universal portfolio returns, verified GBP endpoints, signed irregular external flows, pre/post cash-flow unitisation, exact-period cash gain and XIRR, time-weighted return, annualisation, sampled unit NAV drawdown, role drift and CSV-ready metric audit; Django owner cockpit money-weighted adapter and focused tests.
- [x] **E** strategy-neutral read-only strategy registry and presentation models; redesigned Today / History / Ledger / Strategy pages, one-next-action framing, provenance/disclosures, source-grounded metrics, real transactions with ledger filtering/export, responsive styling and focused owner-isolated UI tests.
- [ ] **F** one-screen onboarding, contributions and conservative T212 import.
- [ ] **G** safe single-transaction owner-confirmed fills and actual-baseline handling.
- [ ] **H** owner destination/alert matrix, idempotent digest, scheduler diagnostics.
- [ ] **I** secure Traefik deployment, optional TOTP, backups/restore.
- [ ] **J** CI/Playwright and incremental ship plan.
- **Instruction:** user wants **one lettered section per turn**. Stop after E now; proceed with F **only** when user says "next". Preserve branch, commit history, PR and safety until tested replacements exist.
- **Architecture decision:** no broker/API integration of any kind, no editable preset/ticker/rule UI, no paper account, no billing or public signup. Fixed code-defined strategy versions; role-based evidence-backed exact lines pinned at enrolment. See `docs/architecture.md` and `CLAUDE.md`.
- **Outstanding verification remains outstanding:** workbook parity, ISA/broker eligibility, real CSV, CI/migrations/browser checks, and deployment. Section A did not test or deploy runtime changes.

## Section C delivery — 2026-10-19

- [x] Fixed v1 Python rules extracted from `tracker/market_signals.py` to `tracker/strategies/nine_sig/v1.py`; existing Django adapter delegates pure quarterly calculations without starting a second strategy implementation.
- [x] Typed `PortfolioHistory`, `NormalizedQuote`, `ApprovedPriceBundle`, immutable rule parameters and `EvaluationResult`; quarter-by-quarter 9% growth and half-of-actual-irregular-contributions audit, reserve/throttle intermediates, 30 Down state, non-tradable TQQQ 100%-up proxy warning, and defined cannot-compute result.
- [x] Strict GBP/GBX price-unit/source/date validation and pinned instrument/strategy identity. An optional independent **Stooq USD TQQQ/SPY reference** adapter requires actual overlap with an independently identified Yahoo observation; never substitutes UK execution prices and never silently persists fallback data. The adapter is a new-source interface, not a guarantee that any independent feed is available.
- [x] Six intentionally empty case rows in `tracker/strategies/nine_sig/fixtures/workbook_parity.csv`; separate quote and external-flow fixtures; automated test compares genuine case data when filled, otherwise explicitly skips only the owner-parity test. Synthetic engine/provider tests added but new CI result not asserted.
- [x] All five required owner-supplied input types, including exact worksheet columns, dates and fixture formats, live in `docs/OWNER_TODO.md` **once**; not blockers for the next build section.
- [x] Section **D** universal metrics delivered separately from Section C. No automatic broker trades, no broker integration or extra cockpit-hardening work in this milestone.

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

**Next scope:** Section **E only** when the owner says "next"; retain this C evidence and do not redo it. Refresh live PR HEAD before editing because another continuation may add changes on the same branch.

## Section D delivery — 2026-10-19

- [x] Implemented pure `tracker/core/metrics.py` (`METRICS_VERSION=universal.metrics.v1.0.0`): immutable verified GBP valuation points with opaque observation IDs, signed dated `ExternalFlow`, ordered `FlowBoundary`, and `PortfolioPeriod` contract. Nothing in this module depends on 9Sig rules, Django ORM, market-data providers, or broker access.
- [x] Money-weighted end-to-end metrics from real external cashflows: net funded, cash gain in GBP, XIRR; a single-deposit simple cumulative return is distinguished from repeated irregular deposits/withdrawals. The app no longer presents a compounded-XIRR proxy as “cumulative return” after irregular funding.
- [x] Exact linked unit NAV from matched pre/post-flow valuations; funding mints/redeems units at the **same verified price**, not investment return. Includes audit trail of values, observation IDs, NAV, issued/outstanding units, status, reason codes, string-serializable rows, sampled flow-adjusted drawdown, period TWR/annualisation and generic allocation percentage-point drift. Ambiguous same-day marks, mismatched prices/units/flow amounts, and absent boundaries never produce an “exact” TWR.
- [x] Django `tracker/cockpit.py::_performance` delegates qualified inception returns to the new generic core using strategy-scoped deposited/withdrawn ledger rows and source-verified **same-day GBP holdings values**. Existing snapshot-only TWR still requires **zero external flows in its snapshot period**, and its audit distinguishes XIRR/that shorter snapshot period.
- [x] Focused arithmetic and negative tests: `tracker/test_core_metrics.py` (deposit/withdrawals/multiple same-day flows, price/source equality, missing boundaries, unitised drawdown, audited units, internal fees, drift, partial/refusal, inception) and `tracker/test_metric_adapter.py` (Django owner isolation, irregular deposits, valuation holds). No new database schema or migrations.
- [ ] Exact portfolio TWR across real owner contributions is **not retroactively computable** from legacy date-only `StrategyValueSnapshot` totals: they do not contain verified pre/post external-flow values. The **calculation engine is ready**; future workflows can supply evidenced boundaries rather than hallucinating them.
- **Additional Section D precision work:** XIRR uses Excel-compatible ACT/365 day counts in `tracker/performance.py`; `tracker/core/metrics.py` refuses malformed cashflow objects/dates, unknown/non-mapping allocations and ambiguous closing-day flow/valuation order, and reports a 100% total loss as -100% TWR/annualised TWR with full sampled drawdown. `gain_vs_net_contributions_pct` is explicitly descriptive, **not** an investment return, and undefined for zero/negative net funding. See `tracker/test_core_metrics_integrity.py` and `docs/metrics.md`.
- **CI evidence:** [#397](https://github.com/Ricas13/wealtharr_v2/actions/runs/39291192519) passed with the Section D code/test changes at `7f77830150f7c20c08af2f29d0b791899db093c3`. Later documentation and concurrent commits trigger their own checks; inspect live HEAD before claiming final green status.
- [ ] Next section: **E**, strategy-agnostic Today / History / Ledger / Strategy-information presentation only.

## Section E delivery — 2026-10-19

- [x] Added `tracker/core/presentation.py` (strategy-neutral, pure render-facing models for action priority, cash-only value, holding display, calculated return labels, confirmed ledger cash effects and historical review statuses). Presentation does **not** evaluate 9Sig or send orders.
- [x] Added fixed-version, code-only descriptions in `tracker/strategies/registry.py`, with the released 9Sig rules and explicit 60/40 **initial/reset reference**, not an invented standing quarterly target. Read-only Strategy page displays exact mapped issuer/ISIN/line/GBX↔GBP metadata and carefully labelled catalogue/ISA evidence, never pretending owner trading permissions are verified.
- [x] Today is a concise owner decision page with actual cash, recorded net contributions, supported gain/returns, a single trade/review/blocked/idle action path, planned next review, strategy allocation/reference explanation and only recorded snapshot charts; the actual contribution form now lets the owner choose the **true deposit date**. No broker operation.
- [x] History separates money-weighted XIRR, flow-safe snapshot TWR, drawdown and cumulative definitions, with clear missing-evidence messages, accessible history and historical alerts **not** offered as active trade instructions. Ledger shows recorded GBP cash changes including fees, owner-scoped filter/sort and CSV export without changing stored immutable events.
- [x] The server-rendered Django views preserve existing ownership filters and strategy signal calculations; `tracker/cockpit_urls.py` exposes the new Strategy guide, `templates/base.html` has consistent owner navigation; responsive dark styling in `static/css/cockpit.css`. Tests in `tracker/test_presentation.py` and `tracker/test_cockpit_pages.py` cover action hierarchy, real ledger cash display, owner isolation and page routing.
- **Scope boundary:** generic **presentation** contracts are in place, while current `_strategy()` still targets the single owner 9Sig instance; broader enrollment/profile changes are expressly **Section F**. No new migrations, changed 9Sig evaluator or automated transactions in Section E.
- **Section E follow-through:** Reused `templates/partials/performance_chart.html` on Today/History; added `templates/partials/data_status.html`, semantically labelled "Next action" and available/blocked metric states, genuine deposit date input, current-vs-initial/reset reference composition (explicit cash role), read-only fixed strategy code descriptions instead of mutable `StrategyPreset.methodology`, source review dates/ISA permission caveats, historical execution date linking to real ledger fills (without pretending their prices match proposals), and accessible `aria-current` navigation. Ledger filtering/sorting and full matching CSV use owner-only queries, guard spreadsheet formula injection and never alter records. `tracker/cockpit.py::_price_status` additionally blocks actionable pages while `YahooSync.last_error` places any required quote on hold. Tests in `tracker/test_section_e_pages.py` cover owner separation, pages, quote hold, filtered export, fixed rules and fractional-share allocation.
- **CI evidence:** [Run #466](https://github.com/Ricas13/wealtharr_v2/actions/runs/39293922625) succeeded on `70915e05fbbf39280cc67d4efbd42b97a5f47fff`, including Django tests, migrations, assets, production-mode smoke and Compose helper validation. Later commits add a penny-rounding allocation comparison and a regression; **do not claim current HEAD fully green** until those workflows complete.
- **Section E honesty:** No live Trading 212 positions or workbook values have been verified. Responsive CSS includes 360px rules but an interactive mobile/keyboard/Playwright visual check has **not** been performed. A self-hosted Inter font asset is not included yet; avoid claiming this requirement complete. Existing `_strategy()` is still deliberately 9Sig-specific until Section F's new setup and registry integration. Exact portfolio TWR requires documented flow-aligned valuations, not historical chart points.
- [ ] Next section: **F**, one-screen real ISA onboarding, recorded contributions and conservative Trading 212 import experience only.

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

1. Read this handoff and `docs/architecture.md` first; inspect latest PR #11 files before changing anything because the branch has been edited concurrently.
2. Implement **Section F only**: one-screen UK ISA/Trading 212 onboarding, real recorded contributions and cautious existing CSV preview/import UI. No broker connection or silent instrument substitution.
3. All external owner evidence is already requested once in `docs/OWNER_TODO.md`; it does not block development.
4. Keep existing commits, then update this document once at the end and stop for "next".

## Critical financial semantics

- Do not equate LQQ3/ERNS with TQQQ/AGG; instruments have different tracking, resets, fees and FX.
- Actual executions require owner confirmation; ordinary quarterly trades never generate a new 60/40 reset baseline.
- New 60/40 baseline needs actual fills, verified later close, GBP instruments, >=0 cash and equity within 5pp allocation.
- Trading 212 CSV import is intentionally conservative and atomic: preview rejects unmapped/FX/uncertain rows.
- XIRR is money-weighted, not time-weighted. Exact TWR/drawdown withheld absent flow-boundary unitisation.
- The spike reference and discretionary 9Sig exceptions still need comparison to owner's workbook. Do not label the rules “verified” until independently confirmed.

## User prompt for a new chat

**Use the Section F-only pasteable prompt at the top of this file.** Keep the user's letter-by-letter stop condition. Later progress may update the next section; never infer sections complete from queued CI.
