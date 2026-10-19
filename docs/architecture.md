# Wealtharr v2 — target architecture (Section A)

Status: **design contract, not implemented** (2026-10-19). Working branch: `feature/owner-9sig-cockpit-resumable`, PR #11. Read `docs/RESUME.md` for the current implementation state. The existing cockpit remains live *in the branch* until replaced in tested steps.

## Product and non-negotiable boundaries

Private, semi-automated tracking for owner-confirmed investments. Wealtharr **never** connects to brokers, submits orders, invents prices/fills/backtests, or silently changes rules. UK / Trading 212 / Stocks & Shares ISA is the first supported profile; 9Sig is the first strategy, not the core architecture. No subscriptions, public signup, generic preset editor, DSL, or paper-account simulator. User-owned records must be filtered by owner, although the first UX serves a single user. All monetary arithmetic is `Decimal`.

The user journey is **Today → recommendation or one clear refusal → I did this → actual-fill ledger**, with an always-available **Record a contribution** action. A contribution is a real recorded cash event, not a scheduled assumption. Risk- and model-specific explanation stays behind expandable details. Missing/stale/mismatched/on-hold data means **no actionable signal**.

## Ownership of logic

```text
Django pages / API / notification scheduler
           ↓ standard EvaluationResult only
Core application services: owner checks, registry/version resolver,
valuation/metrics, historical replay, data-validation, workflows,
deduplication/imports, notification routing, audit and persistence
           ↓ immutable PortfolioHistory + approved PriceBundle
strategies/nine_sig/v1.py :: evaluate(history, prices, fixed_params, as_of)
           ↓ pure EvaluationResult (no Django, IO or side effects)
Core persists versioned signal and renders Today / digest / CSV proof
```

The orchestrator chooses the pinned strategy version, enforces the code-defined platform/account mapping, gets approved historical quotes, builds an immutable history snapshot, calls a pure evaluator, and persists its result idempotently. Notification destinations never enter the strategy module. A strategy never writes database rows or sends notifications.

### Target folder layout (keep one Django app initially)

```text
wealtharr/                 # settings, urls, ASGI/WSGI
tracker/
  models.py                # Django model declarations; one migration app
  admin.py                 # operational read-only audit, no strategy-rule editor
  core/
    registry.py            # immutable code-defined strategies and version lookup
    instruments.py         # code-defined platform/account-aware mapping resolver
    portfolio.py           # immutable history, ledger replay, cash/positions
    prices.py              # source adapters, quality holds, approved price bundle
    evaluation.py          # orchestration, persistence and refusal precedence
    metrics.py             # gain, XIRR, exact TWR, annualised, drawdown, drift
    workflows.py           # contribution, confirmed fills, reset baseline
    imports/trading212.py  # CSV mapping, preview, duplicate checks
    notifications.py       # alert matrix, channels, retries
    audit.py               # immutable change and provenance events
  strategies/
    contract.py            # typed dataclasses/Protocol (no Django imports)
    nine_sig/
      v1.py                # pure rules; 60/40 initial, quarterly signal etc.
      fixtures/            # spreadsheet parity cases by quarter
  views/
    today.py history.py ledger.py onboarding.py strategy_info.py settings.py
  management/commands/     # price sync, evaluations, due/retry alerts, health
  tests/                   # unit, permissions, parity, price, ledger, workflows
templates/                 # Today / History / Ledger / Strategy / Settings
templates/partials/        # status, next action, chart, fill, data freshness
static/css/site.css        # responsive dark UI; no inline CSS or Node build
static/js/site.js          # vanilla JS or small progressive enhancement
static/fonts/              # self-hosted licensed Inter assets
docs/architecture.md
docs/9sig-methodology.md
docs/FINANCIAL_QA.md
docs/RESUME.md
CLAUDE.md
```

Existing `tracker/models.py` can stay one file while the schema stabilises. Do not split Django model import paths or move to separate installed apps just to satisfy a diagram. Keep server-rendered templates, Django 5.2, PostgreSQL, WhiteNoise and Docker Compose behind Traefik.

## Data model — *target*, not present schema

All owner data has an explicit `owner_id`, or an immutable FK chain through a user-owned `StrategyInstance`; every view/service scopes on owner. Market-data observations may be cached per owner initially; do not share portfolio state globally. `User` is Django's auth user. Examples below give essential fields; standard PK, indexes, timestamps, constraints and explicit FK deletion policies are assumed.

| Record | Fields and purpose |
| --- | --- |
| `Account` | `owner`, `platform_code` (TRADING212), `account_type` (ISA), `country` (GB), `base_currency` (GBP), `label`, `created_at`; one active strategy/account in MVP to avoid double-counting cash |
| `StrategyInstance` | `owner`, `account`, `strategy_key`, `current_version`, `mapping_version`, `resolved_instruments_json` (immutable snapshot of exact lines), `started_on`, `active`, `created_at`; no user weight/ticker/rule fields |
| `StrategyRevision` | `instance`, `from_version`, `to_version`, `old/new_mapping_hash`, `change_summary_json`, `confirmed_by`, `confirmed_at`; include an initial-enrolment revision. Append-only history of explicit version upgrades |
| `LedgerEntry` | `instance`, `kind` (OPENING_CASH / OPENING_POSITION / DEPOSIT / WITHDRAW / BUY / SELL / DIVIDEND / FEE / REVERSAL), `occurred_at` or unambiguous `occurred_on` + source sequence, `instrument_id` or null for cash, `quantity` Decimal(24,8), `unit_price_gbp` Decimal(24,8), `amount_gbp` Decimal(24,2), `fee_gbp` Decimal(24,2), `source`, `external_event_id`, `fingerprint`, `reverses_entry`, `execution` nullable, `recorded_at`; append-only, never edit |
| `PriceObservation` | `owner`, `instrument/reference_key`, `market_date`, `raw_currency`, `raw_unit` (GBP / GBX / USD), `raw_close`, `normalised_close`, `normalised_currency`, `source`, `fetched_at`, `content_hash`; immutable historical quote, not an unproven current mark |
| `PriceDecision` | `observation`, `decision` (ACCEPT / HOLD / REJECT), `reason_code`, `details`, `reviewer` nullable, `created_at`; append-only safety/review provenance; an on-hold price must never enter an evaluation |
| `ValuationSnapshot` | `instance`, `as_of`, `total_gbp`, `cash_gbp`, `net_contributed_gbp`, `position_values_json`, `price_observation_ids_json`, `input_hash`, `recorded_at`; immutable by revision rather than overwritten |
| `StrategySignal` | `instance`, `strategy_version`, `mapping_version`, `as_of`, `signal_key`, `evaluation_hash`, `status`, `reason_code`, `explanation`, `trades_json`, `intermediates_json`, `strategy_metrics_json`, `urgent`, `next_review_on`, `source_ids_json`, `created_at`; immutable, including cannot-compute events |
| `SignalDecision` | `signal`, `decision` (NOT_APPLICABLE / FALSE_POSITIVE / CONFIRMED / PARTIAL), `note`, `actor`, `created_at`; append-only rather than mutating the original signal |
| `ExecutionBatch` | `instance`, `signal`, `confirmed_by`, `executed_on`, `completeness` (PARTIAL / COMPLETE), `created_at`; confirmed actual fill group with linked ledger rows; retries idempotent |
| `StrategyBaseline` | `instance`, `strategy_version`, `mapping_version`, `origin_signal`, `execution_batch`, `effective_on`, `equity_quantity`, `equity_value_gbp`, `safe_value_gbp`, `cash_gbp`, `price_observation_ids_json`, `created_at`; append-only, only verified 60/40 initialisation/reset |
| `ImportBatch` | `owner`, `account`, `source`, `file_sha256`, `column_mapping_json`, `imported_count`, `rejected_count`, `created_at`; individual accepted rows dedupe on `LedgerEntry` identifiers/fingerprints. Dry-run is read-only |
| `NotificationDestination` | `owner`, `channel` (EMAIL / DISCORD / optional PUSH), `label`, `address` or encrypted webhook/token, `enabled`, `verified_at`; SMTP transport configuration belongs to server env, not strategy rules |
| `AlertRoute` | `instance`, `destination`, `alert_type` (QUARTER_END / URGENT / UNACTED / CONTRIBUTION / DATA_PROBLEM), `enabled`, `remind_after_days` where relevant; unique per instance/destination/type |
| `NotificationDelivery` | `owner`, `signal` nullable, `destination`, `event_key`, `status`, `attempts`, `next_retry_at`, `last_error_safe`, `created_at`; unique destination/event for dedupe; at-least-once network semantics |
| `SchedulerState` | `job_name`, `last_started_at`, `last_success_at`, `last_failure_safe`; operational heartbeat, never treated as a market quote or signal |
| `AuditEvent` | `owner`, `instance` nullable, `kind`, `object_ref`, `actor`, `payload_json`, `created_at`; append-only security/config/audit trail not duplicated into financial calculations |

Practical schema constraints:
- Unique `(owner, label)` per account as appropriate, unique `(instance, signal_key, evaluation_hash)`, unique external broker event identifiers per account/source, unique imported fingerprints scoped to owner/account/source; no nullable unique-key loopholes.
- Check `quantity >= 0`, `fee >= 0`, `unit_price >= 0` for accepted economic rows; validate each kind's applicable fields. A reversal is a new inverse entry with a link; never delete or overwrite original financial facts.
- Monetary cash and price currencies are explicit. Convert **GBX to GBP once** using proven raw quote units; an unidentified FX charge or non-GBP execution blocks until reconciled. USD SPY/TQQQ may be **reference-only** and never used as a GBP fill.
- Deterministic replay orders imported events by broker timestamp + stable source sequence, checks positions/cash **at every step**, and rejects missing starting history, short positions and unsupported splits or unknown holdings. Opening positions are explicitly owner-confirmed *starting observations*, **not fabricated historical buys**; earlier performance remains unavailable.
- Financial records and accepted quotes require DB-level immutability protections (including staff/admin paths) plus transactional services. A successful signal must cite exactly which approved observations and version produced it.
- Keep exact XIRR/TWR histories only when actual dated external flows and boundary valuations support them; otherwise show unavailable. Optional ISA allowance warning uses 6 April–5 April and only recorded eligible subscriptions, not claimed full broker/tax knowledge.

**Section B evidence update (2026-10-19):** The first source-controlled role catalogue is now in `tracker/instrument_mapping.py`, keyed by strategy/role/GB/Trading 212/ISA/`2026-10-19.1`. It retains LQQ3.L (growth, **raw GBX**) and ERNS.L (safe, **raw GBP**) with reviewed issuer/broker Invest evidence, immutable dataclasses and explicit unverified **personal Stocks ISA approval** and **Yahoo feed** flags. Read `docs/instrument-research.md` before changing any identity; independent data-quality and version-pinning runtime flows remain Sections C/F.

## Fixed strategy and instrument catalogues

Strategies and mapping rows are **frozen source-controlled Python manifests**, not admin-editable database tables. Registry keys are `strategy_key + semantic_version`; never alter released v1 behaviour without publishing v2. All role weights, parameters, market series needs, schedule, plain-language rules and eligibility rules come from code.

An instrument mapping is keyed by `(strategy_key, role, country, platform, account_type, mapping_version)` and stores **exact** instrument name, ISIN, issuer, trading line / venue, broker code, Yahoo symbol, raw quote unit/currency, normalised trading currency, issuer source URL, broker/catalogue evidence, ISA evidence, verified-on date, and verification status. For role `growth` this is not interchangeable with a similarly named daily-reset ETP. Mappings are resolved **only at onboarding or an explicit migration** and their version/hash and full identity are pinned in `StrategyInstance`; historical signals and baselines keep the old versions. No runtime ticker selection or user-entered tickers. Unsupported/unverified profile means **strategy unavailable**, not best-effort substitution.

The present `tracker/instrument_mapping.py` covers LQQ3.L and ERNS.L and labels Trading 212 Invest catalogue availability as weaker than actual ISA access. **Section B** must independently check all identifiers, exact GBX/GBP quote units, product availability, evidence URLs, dates and alternatives; do not mark the current catalogue "verified" merely because it contains URLs.

Upgrading from v1 to v2 requires a visible rule/instrument diff, owner confirmation, atomic append of `StrategyRevision`, a new current-version pointer, and a clear decision on whether a newly confirmed baseline is necessary. Existing v1 signals, CSV calculations and baselines **must not be rewritten**. Missing historical code version is a `cannot_compute` reason, not permission to run the latest rules.

## Minimal strategy contract (extract from actual 9Sig needs)

```python
@dataclass(frozen=True)
class TradeProposal:
    role: str                   # e.g. "growth" or "safe"; core is agnostic
    instrument_key: str         # pinned exact ISIN + trading line identity
    side: Literal["BUY", "SELL"]
    amount_gbp: Decimal
    approx_shares: Decimal
    indicative_price_gbp: Decimal
    price_date: date
    reason: str

@dataclass(frozen=True)
class EvaluationResult:
    status: Literal["ok", "action_needed", "cannot_compute"]
    explanation: str
    proposed_trades: tuple[TradeProposal, ...]
    as_of: date
    next_review_on: date | None
    reason_code: str | None = None
    urgent: bool = False
    event_code: str | None = None  # opaque strategy event for alert idempotency
    intermediates: Mapping[str, str | Decimal | bool | date] = field(default_factory=dict)
    strategy_metrics: Mapping[str, object] = field(default_factory=dict)
    chart_series: Mapping[str, object] = field(default_factory=dict)

class FixedStrategy(Protocol):
    key: str
    version: str
    display_name: str
    roles: tuple[RoleDefinition, ...]          # label + code-fixed target weight
    parameters: Mapping[str, Decimal | str]   # code-fixed; no owner tuning
    required_series: tuple[SeriesRequirement, ...]
    schedule: ReviewSchedule
    rules_text: str
    def evaluate(self, ledger: PortfolioHistory, prices: PriceBundle,
                 params: Mapping[str, object], as_of: date) -> EvaluationResult: ...
```

`PortfolioHistory` is an immutable read model, **not** a live Django QuerySet: ordered real ledger events, owner-confirmed opening observations, confirmed baselines, earlier frozen signal outcomes/skip history and the pinned instrument identities/version. The latter history is needed by 9Sig's 30 Down two-sell skip and reset logic; it cannot be inferred from trade fills alone. `PriceBundle` contains only approved dated closes and required lookbacks (GBP listed lines, reference-only SPY/TQQQ USD) with observation IDs, unit metadata and freshness evaluation, never network functions. The caller supplies all inputs and fixed params. Pure evaluation cannot read `datetime.now()`, ORM, files or external networks and cannot write state.

`cannot_compute` **always has one priority `reason_code` + one plain-language explanation, zero trades, `urgent=False`**. Use deterministic refusal precedence: unsupported profile/version → outside holdings/invalid replay or negative cash → required price on hold or wrong units → missing history/price → stale/mismatched dates → pending unverified reset. Record diagnostics internally; show the user just the first actionable cause. `ok` may report no action, and `action_needed` has one or more trade proposals; strategy-defined urgent warning (e.g. a provisional TQQQ spike) may be `ok` with `urgent=True` and **zero trades**, so the alert invites manual verification without presenting an executable reset.

The engine reports money and shares with explicit rounding/precision and a named intermediate set (signal line, equity valuation, gap, buying power, throttle, 30 Down window, spike reference, reset flags). Persist a stable, CSV-exportable ordered intermediate representation with Decimal values serialised as **strings**, alongside snapshot fingerprints and source dates. A minor downstream UI change cannot silently recalculate an old signal.

## One-reason user refusals

A `cannot_compute` evaluation returns the highest-priority reason only. Core interpolates values from recorded facts and never suggests guessing or executing old trades.

| Code | Plain-language Today message |
| --- | --- |
| `STALE_PRICE` | "The latest approved price for {instrument} is dated {date}. I can't give you a trade instruction until prices are refreshed." |
| `MISSING_HISTORY` | "I need your starting holdings or a missing earlier transaction before I can calculate this review. Import or record the actual history first." |
| `OUTSIDE_STRATEGY` | "{instrument} is held in this account but isn't part of this strategy. Reconcile it before I suggest trades." |
| `NEGATIVE_CASH` | "Your recorded cash is £{amount} below zero. Check for a missing deposit, trade or fee before continuing." |
| `PRICE_ON_HOLD` | "The price for {instrument} is on hold because {brief_reason}. I won't use it to calculate a trade until it's verified." |

When a price is on hold, show its raw source date/currency under the expandable data explanation, not a guessed fallback price.

## Transaction, data quality and notifications

- Prices: pluggable source primary + independent fallback; raw observations are retained, sanity checks fail closed on stale/mismatched date, large moves and GBP/GBX confusion, and a human-reviewed release is an append-only decision. A fallback quote is **not** automatically trustworthy merely because Yahoo failed.
- Quarterly signal: evaluate only completed quarter using correct **as-of closes and ledger replay as-of that date**. Intraperiod events are independent strategy-defined warnings. Never apply today's holdings/prices to a historical review.
- Confirm "I did this": GET presents pinned signal and quotes; POST obtains row lock, verifies ownership/version, freshness, chronology, source instruments, sufficient cash/positions and user-adjusted fills, then writes execution batch, ledger rows, decision and (if justified and verified) reset baseline in **one database transaction**. Non-reset quarterly trades do **not** create a baseline. Partial fills remain explicitly partial and never masquerade as a completed reset.
- A reset requires actual fills plus a sufficiently recent **post-fill verified close** for each sleeve and equity within the permitted target tolerance. If post-fill observations do not exist yet, **do not claim the reset completed**: clearly refuse final reset confirmation, preserving the draft input for retry; do not create synthetic post-trade prices/baselines. This is an unavoidable real-world freshness gate to revisit in the Section G UX.
- CSV import: mapped Trading 212 export is previewed dry-run, validates row-by-row, compares stable IDs and economic fingerprints against imported **and manually entered** events, and atomically records only an unambiguous accepted batch. Missing support (FX, split, interest, foreign currency, ambiguous dates) is reported, never guessed.
- The scheduler persists idempotent `StrategySignal` events, dispatches quarter-end/urgent/unacted/contribution/data-problem alerts through per-instance `AlertRoute`, retries deliveries, emits structured safe logs/Sentry events, updates `SchedulerState`, and pings a configured external healthcheck only after successful completion.
- UI, CSV exports and notification templates depend on `EvaluationResult` fields and generic role names; **no 9Sig conditionals** in core views, forms or messages. Today displays value, contributions, gain, universal metrics, one next action, target/actual, next review and freshness. No "verified" rules badge until workbook parity is passed.

## Design-only extensibility checks

**Fixed 60/40 rebalance:** roles `growth` and `safe`, fixed `0.60/0.40` weights, quarterly schedule and approved portfolio closes. Evaluate difference between actual and target GBP weights, propose paired trades rounded to lot/precision rules, or return `ok`. No custom fields or strategy-specific core logic needed. Because the current contract is designed for 9Sig, threshold/no-trade-band, holiday-calendar and cash-routing rules must be explicit *inside this new strategy version*, not configurable by end users.

**200-day moving-average trend:** fixed portfolio role(s), daily 200-session price series (adjusted vs unadjusted explicitly pinned), review cadence and deterministic risk-on/risk-off holdings. `evaluate` can emit BUY/SELL using approved history, or `cannot_compute` with fewer than 200 eligible observations. Contract gap likely to emerge: multiple independent market calendars, execution-timing convention (close vs next session to avoid look-ahead), price-adjustment metadata, corporate actions and more general event/urgency semantics. Add these only when a concrete strategy needs them; **do not build a DSL**.

## Existing code → migration plan (do not delete until replacement passes tests)

| Current implementation | Decision |
| --- | --- |
| `tracker/market_signals.py` | **Extract** quarterly maths, 30 Down and spike into pure `strategies/nine_sig/v1.py`; move Yahoo ingestion/quality to core prices; move ORM, scheduler and dispatch to core evaluation. Retain reference/parity tests |
| `tracker/instrument_mapping.py` | **Replace** with immutable versioned role + country/platform/account-type manifest (Section B evidence gate); keep stable historical mappings |
| `StrategyPreset`, preset admin/forms, end-user ticker/weight controls | **Delete** after code-defined strategy registry and onboarding are in use |
| `PAPER` account mode, `tracker/paper.py`, paper-only views/templates/tests, paper migrations | **Delete** when new disposable DB schema is applied; preserve no paper state |
| `PriceMark` + `MarketObservation` + `YahooSync` | **Consolidate** only after accepted/history quote quality and scheduler tests pass; preserve reference-history 30 Down/spike behaviour |
| `WealthSnapshot` + `StrategyValueSnapshot` | **Consolidate** into flow-safe per-instance valuations after independent return-metric tests |
| `StrategySignal`, `StrategyBaseline`, `RebalanceExecution`, `BrokerImportRow` | **Keep semantics**, refactor to immutable signal/outcome/baseline, execution and deduplication model rather than deleting safety controls |
| `SystemIntegration` singleton, coarse `NotificationPreference` | **Replace** with env SMTP transport + user destinations and per-instance routing; secrets remain encrypted |
| `tracker/cockpit.py`, `services.py`, `cockpit_notifications.py`, legacy duplicate routes/templates/CSS | **Split/repoint**, then remove unused entry paths after Today/onboarding/import/confirmation and alert tests pass |
| `tracker/migrations/0001`–`0009` | **Replace/squash** only with disposable local DB, after code/test integration; production DB must be explicitly backed up and deliberately reset, never silently dropped |

## Safety / spreadsheet comparison / acceptance

Current implementation has credible safeguards but is **not independently verified**: a string search in a signal message identifies 60/40 resets, recent quotes can be too old for execution, a provisional TQQQ previous-quarter closing price is **not** necessarily Jason Kelly's actual execution spike reference, 30 Down discretionary history is stateful, and UK LQQ3/ERNS do not recreate US TQQQ/AGG. Existing `cockpit.py` also depends on 9Sig names/roles, so architecture claims are targets, not accomplished work.

For **Section C** compare workbook results quarter by quarter: initial 60/40 anchor, 1.09 quarterly growth, half *actual* irregular contributions at quarter end, 81% conditional throttle, SPY prior-eight-quarter peak, two skipped sells, eight-quarter expiry, spike, reset and manual overrides. Use reproducible fixtures with source dates and expected intermediate columns. For **Sections D/G** compare actual broker deposits/fills/cash balances, XIRR/TWR cashflow boundaries and reset post-fill quote dates. Stop a signal if any required price, version, holdings history or opening state is unknown.

Implementation gates: (1) schema integrity and owner isolation; (2) frozen manifests + exact mapping evidence; (3) pure rules and workbook parity; (4) replay/import/quote holds; (5) generic Today and single-transaction confirmations; (6) notification idempotency; (7) CI Django unit tests, Playwright 360px flows, security/backup restore. No rule is labelled independently verified before its own parity gate passes.
