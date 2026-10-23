# 3× Nasdaq Trend + DCA — rule version `nasdaq_trend_dca.v1.0.0`

A normal Wealtharr strategy (Add strategy, Today, Study, Ledger, History). It
never places a trade or invents a fill; the owner records actual fills.

Source: the r/LETFs post "SPY 200SMA +4%/-3% → TQQQ/QQQ" and the author's later
clarification of the QQQ safeguard (below). Rules are in
`tracker/strategies/nasdaq_trend_dca/v1.py`.

## Rules

Signals use completed US sessions, SPY and QQQ raw closes in USD, and the
200 closes ending on that day. Orders are for the next session.

1. SPY above 104% of its 200-session average: hold the 3× fund.
   SPY below 97%: sell everything to cash. Between the bands the earlier
   state holds.
2. While SPY is on, QQQ at or above 130% of its own average: hold the 1× fund
   instead; at or above 140%: cash. The guard **latches** until SPY has exited
   and re-entered (the author's clarification: do not re-leverage just because
   QQQ cooled off). It is not in the original post's text.
3. After an exit, the cash is invested in the 1× fund in 12 equal monthly
   instalments, the first one month after the exit. Twelve is Wealtharr's
   choice within the author's "6–12 months".

Because the guard is path-dependent, the state is replayed from the full stored
SPY/QQQ history (synced from 2010), never from a trailing window. A series with
gaps in the last 200 sessions, a price hold, or no band crossing yet blocks the
review.

## Instalment schedule (Wealtharr's extension)

The post doesn't cover cash added while out of the market. Wealtharr treats
every cash inflow since the exit (cash held at the exit, sale proceeds,
deposits, dividends) as its own 12-month schedule from its own date. The amount
due is `Σ inflow × months_elapsed / 12 − actually bought`, read from the
ledger alone, so a missed month is caught up and nothing is repeated. After 12
months everything is invested. Only 3× holdings are sold once instalments have
begun; the 1× holding then is what the instalments bought.

## Instruments

| Account | 3× role | 1× role | Signals only |
| --- | --- | --- | --- |
| Trading 212 (GBP) | LQQ3.L (GBX) | EQQQ.L (GBX) | SPY, QQQ (USD) |
| Fidelity (USD) | TQQQ | QQQ | SPY, QQQ (USD) |

LQQ3 is not TQQQ (different structure and costs); UK results are not TQQQ
results. The EQQQ listing on Trading 212 and its ISA permission are not
independently verified: confirm it is buyable in the account before use.

## Workflow

The hourly review saves a `QUARTERLY`-kind review only when an order is due.
A review stays actionable for 7 days (`StrategyModule.current_review_floor`),
so fills can still be recorded after the next close has been fetched. An
unchanged pending instruction is kept (no daily repeat alert); a changed one
supersedes it before any fill. Once fills are recorded the review finishes
through the normal checked finalisation, and a new review waits for that.

Study replays the same pure rules on stored history with next-session fills.
It is hypothetical and is labelled so on the page.
