# Adding a strategy

The core is strategy-agnostic. Generic code (dashboard, alerts, hourly job, reviews,
fills, setup, Study, performance) reaches a strategy only through its **plug-in**,
found with `tracker.strategies.registry.module_for(instance)`. A test
(`tracker/test_strategy_plugins.py`) fails if a generic module imports a strategy's
rules directly, and runs a test-only three-fund strategy through the whole loop.

Worked examples: `strategies/nine_sig/` (quarterly signal line; `strategies/three_sig/`
reuses its live review with its own rules and `reset_equity_fraction`),
`strategies/all_weather/` and `strategies/golden_butterfly/` (fixed weights, yearly
rebalance). A fixed-weight strategy needs only a `v1.py` with `VERSION`, `WEIGHTS`
and an `evaluate` calling the shared pure `strategies/fixed_weights.py::rebalance`,
and a two-line `plugin.py` using `strategies/yearly_plugin.py` (`cadence="monthly"` or `"quarterly"` checks
after each month or quarter end instead, as `strategies/hfea/` does; leveraged funds can be rebuilt
before launch with `Source(proxy_leverage=3, proxy_charge=...)`; `strategies/permanent_portfolio/` uses it with a band rule). A strategy without
fixed weights (`strategies/dual_momentum/`) sets its `role_targets` to 0 ("By signal"
at setup) and gives its plug-in `current_weights` for the opening buy and allocation.

## What a new strategy needs

1. **Rules description**: add a `StrategyDescription` to `_RELEASED` in
   `tracker/strategies/registry.py` with a version, plain-English rules and its
   `role_targets` (role → weight). The roles listed here are the funds it holds.
2. **Rules code**: a pure module under `tracker/strategies/<name>/` (no DB, network
   or clock), as for 9Sig.
3. **Plug-in**: a `StrategyModule` (`tracker/strategies/base.py`) wiring it in, then
   add it to `_plugins()` in the registry. The essential fields:
   - `review(instance)`: hourly; update the strategy's price marks and save a
     `StrategySignal` (kind `QUARTERLY` for a scheduled review) with `actions`
     (`side`, `symbol`, `quantity`, `estimated_gbp`). Must be idempotent.
   - `next_review(day)` and `last_review_before(day)`: the schedule
     (`tracker/core/calendar.py` has quarter and year helpers).
   - Daily strategies also set `current_review_floor(day)` so a proposal stays
     current for a few sessions (otherwise only the last review is current), and
     `allows_cash_only` when a cash regime with no holdings is legitimate.
   - Optional: `reference_symbols` (index series the rules need), extra
     `dashboard_template` and `dashboard_context`, `supports_reset`,
     `review_detail` and `review_export` ("How this was calculated"), `study`.
   - Changing a released strategy's behaviour (even only its schedule) needs a
     new rule version module. List the old version's plug-in in `by_version`
     and the old version in the description's `earlier_versions`, so instances
     already pinned to it keep its schedule, reviews and Study until explicitly
     upgraded (see `permanent_portfolio/plugin.py`).
4. **Funds** (CLAUDE.md boundary 2), in `tracker/core/catalogue.py`: one `Listing`
   per exact trading line (ticker, ISIN, raw quote unit GBX or GBP, issuer link)
   with a `brokers` map of broker key → evidence URL, then list each role's
   listings in preference order in `ROLE_LISTINGS`, plus a `StrategyOffer`. For a
   broker and account type, the first listing with evidence for that broker is
   used; setup only offers the strategy when every role resolves. Quote units feed
   the price checks automatically. A USD-quoted line (`raw_quote_unit="USD"`) is
   stored in pounds at that day's Yahoo `GBPUSD=X` close; a day without a rate has
   no price. Keep the rules true to the original; only the
   funds are local equivalents (say so in the caution).
5. **Study** (required for every strategy): the plug-in's `study`. A fixed-weight
   strategy passes a `tracker/study_fixed.py::FixedWeightStudy` with one `Source`
   per role (the fund, and a stand-in for before it existed, flagged on the page),
   and adds the symbols to `study_data.STUDY_SYMBOLS`. A test fails if a released
   strategy has no Study.
   Study data rules: prices are never carried forward more than a week (days
   without a fresh price for every fund are left out, so a Study ends on its
   last fresh day); downloads drop an isolated bad row and refuse a lasting
   implausible one-day move; any owner-confirmed step the Study assumes (such
   as a reset) is stated on the page and counted per run.
6. **Tests**: pure rule tests, plus an end-to-end test like the one in
   `test_strategy_plugins.py`.

## Currencies

A broker has a country and a currency; every account at it is tracked in that
currency (prices, ledger, history, alerts, the symbol on its pages). Listings are
per country (`ROLE_LISTINGS[(strategy, role, "US")]`), with `wrappers` set to that
broker's account types. A plug-in's `study` may be a `{currency: engine}` map, one
engine per account currency with that country's funds. The Overview adds up one
currency only and lists the others.

## Adding a broker

Add a `Broker` to `tracker/core/brokers.py` (name, country, account types). It is
listed in setup straight away but offers nothing until listings cite evidence that
it carries them: add its key and evidence URL to each listing's `brokers`. Never
assume a broker carries a fund because another one does.

## What you get for free

The one-screen setup with pinned funds, go-live, the opening-buy split at the
target weights, the price-freshness checks for every fund, the dashboard cards, the
quarter-end alert and reminders, "I did this" fills, automatic finishing after the
next close, ledger, performance and value history, and noindex/owner scoping.

9Sig-only pieces stay in its plug-in: the signal-line card and quarter table, the
60/40 reset, the 30 Down reference, its calculation breakdown and its Study.
