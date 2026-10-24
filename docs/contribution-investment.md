# Investing confirmed monthly deposits between periodic reviews

## Owner workflow
1. Record an **actual** deposit in Today -> Add money or Ledger. The planned
   monthly target alone NEVER records cash or creates an advisory trade.
2. Existing live holdings + trustworthy, current pinned quotes + no outstanding
   regular review: Today displays **Invest your new deposit**. Budgets are in
   account currency and never exceed confirmed available cash. All BUYs remain
   manual and must be recorded with their actual broker fills/prices/fees.
3. After a partial recorded BUY, the remaining deposit-funded cash can still be
   recommended; once the cash is used there is no new-deposit instruction.
   Edits, withdrawals and fees reduce the conservative available deposit pool.
   Cash from a sale/dividend is not misidentified as a fresh deposit.
4. If quotes, pinned instruments, market-data status or monthly selection cannot
   be trusted, no actionable guidance is generated. An existing trade review,
   reset or partial fill takes precedence.

## Strategy-specific *new cash* policy
These are **new-cash purchase policies**, not changes to the released recurring
strategy signals, bands or resets.

| Strategy | While waiting for next normal review |
| --- | --- |
| 9Sig | Buy pinned reserve fund (GBP: ERNS) with new cash. No early LQQ3 rebalance |
| 3Sig | Buy pinned safe fund. No early 3% quarterly signal |
| All Seasons | Allocate across currently underweight sleeves, buys only |
| Golden Butterfly | Same underweight-first rule across its five sleeves |
| HFEA | Underweight-first new cash, no leveraged sells or early quarter rebalance |
| Permanent Portfolio | Use underweight-first cash, without triggering the annual 15-35% test early |
| Dual Momentum | Buy the most recent fully verified month-end chosen sleeve; never guess |
| 3x Nasdaq Trend + DCA | Existing daily algorithm remains authoritative; new cash joins the daily selected holding or its dated 12-instalment DCA schedule, or stays in cash during the cash guard |

New money for fixed-weight strategies is divided in proportion to positive
target deficits **after** adding the cash to the portfolio total, rounded down
to the penny with remaining pennies assigned to the largest deficit. There are
no sales, no borrowing and no change to scheduled review outcomes.

## Notifications and timing
- Owner must enable at least one destination (email, Discord, Telegram) **and**
  the contribution route for the specific strategy. Existing routes and the
  new-strategy default (all contribution destinations OFF) are preserved; no
  automatic notification opt-in is introduced.
- The hourly notification scheduler sends a once-per-deposit **nudge**, only
  when verified new-money guidance is available and no prior trade is pending.
  It does NOT email approximate orders or force immediate trading.
- Successful per-channel sends are deduplicated; retries check whether the
  cash is still waiting and cancel a now-stale prompt.
- The separate monthly **missing-deposit reminder** still starts from day 3
  when the plan is underfunded; it never assumes a bank transfer happened.

## Historical Study consistency
- The 9Sig/3Sig Studies invest the new monthly contribution into the safe sleeve
  on the first modelled trading day of the month. Signal-line cash-flow maths,
  reset logic and quarter-end reviews remain the same.
- Fixed-weight Studies use the same pure underweight allocation policy to buy
  fractional shares immediately from the new cash without selling.
- Dual Momentum Study buys the last verified month-end selected holding.
- The existing Nasdaq daily Study already incorporates monthly contributions
  into its daily regime / DCA behaviour.
- **Previous published backtest values can change**: the old Studies expressly
  held contributions in cash until the next review. Changes to the contribution
  execution assumptions should be labelled and previous screenshots must not
  be presented as representing the new model.
- All simulations still assume close-based fills, historical proxy limitations
  and no dealing costs; owner cash and broker fills are never inferred.

## Financial controls
No broker integration, real trade, automatic deposit, new strategy category or
modification of historic ledger entries. Do not use Today recommendations
without checking the broker's current dealing price, product access and fees.
Regular quarterly/yearly strategy signals remain a separate, higher priority
workflow. All notifications and purchase suggestions are advisory only.
