# 3× Nasdaq Trend + DCA — Wealtharr strategy version 1

This is a normal source-controlled Wealtharr strategy (same Add Strategy,
Today, Study, Ledger, History and Settings as 9Sig). It is not a broker
integration, never places a trade and never invents a fill. It is a fixed,
documented interpretation of a Reddit author's approach.

## Source and scope

- Original: https://www.reddit.com/r/LETFs/comments/1nhye66/
- Author's safeguard clarification:
  https://www.reddit.com/r/LETFs/comments/1s5h2js/
- Release ID: `nasdaq_trend_dca.v1.0.0`.
- Daily completed **SPY/QQQ US trading sessions**. No current-session
  incomplete bars; decisions may be acted on only in a following eligible
  session on the actual local instrument.

## Versioned rules

1. Compute simple averages of the last 200 session closes (including today's
   COMPLETED session) for SPY and QQQ separately.
2. If SPY strictly exceeds +4% relative to its SMA200, switch to 100% of the
   leveraged Nasdaq role. If SPY falls strictly below -3% relative to its
   SMA200, sell leveraged holdings and hold the confirmed proceeds in cash.
   Inside the band, remain in the previous regime.
3. In ON regime, if QQQ >= +30% above its SMA200, switch to the unleveraged
   Nasdaq role; if QQQ >= +40%, sell and hold cash. Both safeguards **latch**
   until a subsequent SPY -3% exit and then SPY +4% re-entry.
4. After an OFF exit, buy 1/12 of the **actual, confirmed cash reserve from
   that exit** in unleveraged Nasdaq at each completed monthly anniversary.
   A weekend / end-of-month anniversary is recognised at the next eligible
   trading opportunity. Unfilled instalments remain due; only linked, owner-
   confirmed actual fills count as completed. Previous instalments are **not
   continuously rebalanced** as QQQ prices change. A new entry interrupts DCA.
5. Fresh deposits after an exit do not enlarge its original DCA reserve. Any
   position or cash mismatches block, pending manual review.

The original author describes 6–12 months and another personal six-month
variant. Twelve equal monthly instalments are **Wealtharr's fixed v1 choice**,
not a claim of a unique official specification.

## Instruments and accounting

| Account | Leveraged role | Unleveraged role | Reference-only signals |
| --- | --- | --- | --- |
| UK Trading 212 ISA/GIA | WisdomTree LQQ3.L (GBX) | Invesco EQQQ.L (GBX) | SPY & QQQ (USD) |
| US Fidelity brokerage/IRA | TQQQ (USD) | QQQ (USD) | SPY & QQQ (USD) |

Both UK trading lines convert GBX to GBP exactly once. Their structures,
tracking, financing, dividends and currency effects differ from US ETFs;
UK results **cannot be presented as historical TQQQ returns**. All real holdings
and proposed quantities use the pinned exact local instruments and verified
market closes. EQQQ distributes dividends: the actual amounts are separate
ledger cash entries. Product/broker availability is not a guarantee of the
owner's personal product permissions.

Signals use vendor-observed **chart close**, the TradingView basis, not
dividend-reinvested adjusted close. Study uses adjusted fund closes for
hypothetical reinvested performance and has no pre-launch fabricated 3× fund
history. All Study trades fill using subsequent bars (never today's US close
at an already-closed London venue).

## Safety and operator checks

The hourly job refreshes reference + held-product quotes and refuses trading
instructions if sources, raw quote units, daily alignment, price freshness,
revisions or ledger history are unavailable. Strategy signals persist as the
existing review type for use with audited single-fill confirmation; pending
reviews are not silently superseded. Progress is durable per strategy; each
buy/sell must be manually confirmed and then reconciled against post-fill
verified prices. Existing notification opt-in channels remain in force.
The current Study is hypothetical and is not a substitute for executed GBP
or USD portfolio performance.

**Do not deploy this unreviewed PR** if migrations, Python tests or browser
owner-journey CI are not green. Review the local-instrument quote unit,
personal ISA/GIA eligibility and whether the frozen rules fit the intended
investment before using actual money.
