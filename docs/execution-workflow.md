# Section G — actual broker fills and immutable reset baselines

Updated 2026-10-19. **No broker link, API, order placement, simulated transaction or invented fill** is supported.

## Manual execution flow

1. Only the latest completed calendar-quarter **QUARTERLY** advisory with supported released instruments, up to four positive `BUY`/`SELL` proposed actions, may open the guided form. A rejected or historical signal is not an execution.
2. Owner personally executes any trades in their broker, and returns to Wealtharr. Each form records **one actually executed fill**: selected server-defined action index, real execution date, actual unit quantity, actual **GBP/pounds per share** price (not LQQ3's possible GBX pence quote), actual GBP fees and an idempotent confirmation UUID. Suggested unit counts remain visibly estimates; price/date are never silently copied into the real-fill inputs.
3. `tracker/cockpit_execution.record_single_fill()` locks owner-scoped strategy and signal, validates current review/asset identity, rejects future or pre-review fills, excess units, negative cash or oversells, and saves a `LedgerEntry` plus immutable `SignalFill` link in **one atomic transaction**. Same token/same details is a no-op; same token/changed details is rejected. Real fills are not rolled back just because later planned actions were unfilled.
4. **PARTIAL:** until all proposed quantities are actually recorded, no `RebalanceExecution` nor `StrategyBaseline` exists. Other transactions after the quarter-end (outside linked fills) require manual reconciliation, not silent assumption that the old proposal still applies. A partial-fill Today link is explicitly bookkeeping; it does **not** present the old proposal as a new trading instruction or bypass held market prices.
5. **COMPLETE:** owner separately clicks finish. Server again locks strategy/signal, validates exact proposed totals, rejects unrelated after-review ledger events, proves both GBP sleeve quotes are source-backed `MarketObservation` values with the same **post-latest-fill** date, rejects stale/mismatched/on-hold quotes and unsupported positions or negative cash; creates `RebalanceExecution` exactly once. If completion fails the actual linked fills remain, but the completion marker and reset baseline do **not** appear.
6. **RESET:** only a signal with explicit `StrategySignal.reset_reason`, populated by the versioned strategy evaluator on creation, can produce a baseline, and only in that separate finalized transaction. Actual growth weight must be within 5 percentage points of 60%; the baseline stores actual equity quantity, valued equity, safe sleeve, cash, pinned strategy/mapping version and exact observation IDs/source/date/GBP unit in `quote_evidence`. Ordinary quarterly completions create **no** baseline.
7. The old bulk execution POST and legacy free-text `confirm_strategy_reset` link are **retired**. Historic rows whose text *suggests* a reset but which predate the explicit structured flag **are not auto-upgraded or re-anchored**. Provisional SPY/TQQQ reference warnings, including intraperiod 100%-up spikes, are not executable reset instructions.

## Deliberate refusal examples

| Condition | Result |
| --- | --- |
| Partial fill with proposed units remaining | Real fill saved and linked; **no** completed review or baseline |
| Retry same UUID and all same fields | No new ledger entry or duplicate fee |
| Same UUID with different units or fees | Reject; preserve first actual fill |
| Quantity above original proposal | Reject; owner must reconcile genuine deviation, not fabricate balancing trades |
| Sale above owned quantity or purchase above recorded GBP cash | Roll back attempted fill and link |
| Imported/manual deposit or separate trade after the quarter-end | Block continuation until full account reconciliation |
| Invalid/stale/mismatched GBP close or close at/before last executed fill | Save no completion or baseline |
| Reset in message text but empty `reset_reason` | Can complete **ordinary** verified fill accounting only; never create a new strategy baseline |
| Other owner's strategy/signal or historical quarter | Not authorised to record new fills |
| Provisional spike warning | No transaction order and no automatic 60/40 reset |
| Unknown code/mapping version or changed enrolled ISIN manifest | Block action |

## Supported tests, limitations and next steps

- `tracker/test_g_single_fill.py`: actual partial quantities, owner isolation, UUID idempotency, excessive fill rollback, simulated `SignalFill` persistence failure **rolls back the ledger event too**, externally modified ledger, source-backed actual reset, no reset without flag, post-fill market-evidence holds.
- `tracker/test_cockpit.py`: prior all-at-once tests updated to use only the single-fill adapter; verifies ordinary vs reset, over-selling and historical signal refusals.
- `tracker/test_strategy_baseline.py`: historic reset/spike shortcut is now deliberately **non-writing**.
- Migration `0011_signal_fill.py` adds fill links and baseline provenance. `0012_signal_reset_reason.py` adds explicit rule-produced reset reason; it **does not** backfill questionable old free-text signals.
- Remaining owner-only acceptance: compare real broker fills/cash/fees, live GBP quote provenance and genuine workbook parity before relying on actual account recommendations. Those are listed **once** in `docs/OWNER_TODO.md`. No external broker permission can be established by these tests.
- **Partial completion policy:** This v1 only calls a review complete when *all suggested units* have actually been filled. If an actual broker order is intentionally executed at a different amount, record the truth in the ledger and reconcile; do not invent the missing units or falsely mark the review complete. Future strategy-specific deviation acceptance would need an explicit audited revision rather than silent tolerance.
- Section H may reuse completed/partial status for owner alerts, but may not mark old/unverified proposals as actionable or dispatch orders.
