# Adding a strategy

The core is strategy-agnostic. Generic code (dashboard, alerts, hourly job, reviews,
fills, setup, Study, performance) reaches a strategy only through its **plug-in**,
found with `tracker.strategies.registry.module_for(instance)`. A test
(`tracker/test_strategy_plugins.py`) fails if a generic module imports a strategy's
rules directly, and runs a test-only three-fund strategy through the whole loop.

Worked examples: `strategies/nine_sig/` (quarterly signal line) and
`strategies/all_weather/` (five fixed weights, yearly rebalance: `v1.py` rules,
`plugin.py` wiring, funds in `core/catalogue.py`).

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
   - Optional: `reference_symbols` (index series the rules need), extra
     `dashboard_template` and `dashboard_context`, `supports_reset`,
     `review_detail` and `review_export` ("How this was calculated"), `study`.
4. **Funds** (CLAUDE.md boundary 2): one reviewed `CatalogueLine` per role in
   `tracker/core/catalogue.py`, with exact ticker, ISIN, raw quote unit (GBX or
   GBP) and evidence links, plus a `StrategyOffer`. Setup only offers the strategy
   when every role has a reviewed line. Quote units feed the price checks
   automatically.
5. **Tests**: pure rule tests, plus an end-to-end test like the one in
   `test_strategy_plugins.py`.

## What you get for free

The one-screen setup with pinned funds, go-live, the opening-buy split at the
target weights, the price-freshness checks for every fund, the dashboard cards, the
quarter-end alert and reminders, "I did this" fills, automatic finishing after the
next close, ledger, performance and value history, and noindex/owner scoping.

9Sig-only pieces stay in its plug-in: the signal-line card and quarter table, the
60/40 reset, the 30 Down reference, its calculation breakdown and its Study.
