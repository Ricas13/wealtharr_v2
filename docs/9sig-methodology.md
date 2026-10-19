# Wealtharr 9Sig methodology — owner edition (research 2026-10-18)

**Purpose.** Calculate reviewable owner-account rebalance proposals, not place orders.
Rule priority is: (1) Jason Kelly's publicly documented 9Sig methodology,
(2) the uploaded "signal strategy simulation sheet v1.07.xlsx" and its exact
spreadsheet formulas, (3) Reddit community clarifications, explicitly labelled.
The Kelly Letter subscriber-only current trading instructions have NOT been
accessed. The owner can mark an incorrect alert and arrange a code PR.

## Evidence and calculations

| Issue | Current owner rule | Basis / qualifier |
| --- | --- | --- |
| Portfolio sleeves | Initial 60% leveraged Nasdaq equity, 40% safer reservoir | Kelly 2024 post and independent workbook demonstration; **not** a promise to keep a 60/40 allocation each ordinary quarter. |
| Quarterly stock signal | Previous signal line multiplied by **1.09**; half the NEW contributions deposited during that quarter added after compounding | Workbook DEMO 2010++ cells AV4 etc; 2010 sample: £60,000 → £65,400 → £71,286 → £80,701.74 with £6,000 Q3 contribution. |
| Rebalance | Sell stock value above the signal line, buy the shortfall below it, funded by stock/safe reservoir | https://jasonkelly.com/2017/01/how-my-signal-system-works/ |
| When to rebalance | Only completed calendar quarters with verified GBP close for the actual holdings, verified USD reference closes, recorded REAL owner fills | Owner tracking policy; no guessed past fills, no broker connection. |
| Buying throttle | When normal stock buy shortfall is below 90% of current cash + bonds, fund full amount. For larger purchases under the reservoir's value, cap the buy budget at 90% × (1 − 10%) = **81% of total buying power** | Uploaded workbook **BF4** references BR3+BA4+BV4 and C11 (0.1). This is NOT simply 90% of safe assets on every purchase. |
| Shortfall exceeding 100% available buying power | **Owner-approved v2:** sell ALL safe units, spend all existing cash and buy as much growth equity as possible, flooring to the existing unit precision. No shortage-induced 60/40 reset, even during 30 Down; prompt an advisory reset review. | Deliberate divergence from workbook v1.07 Y/Z reset/HOLD. Below-90% full funding and 90%-100% 81% throttle remain unchanged. |
| 30 Down trigger | SPY's quarter-ending close at or below 70% of the highest quarter close of the **preceding 8 quarters** (not the UK LETP and not TQQQ). It needn't have fallen from the immediately preceding quarter. | Jason Kelly 30 Down video https://jasonkelly.com/2020/04/the-30-down-rule/ ; research https://bestfolio.app/blog/kelly-signal-danger |
| 30 Down response | Skip next TWO would-be equity sells while ordinary buys continue. **No mid-quarter sell-half rule.** On the third would-be sell, review a 60/40 reset; review a reset on eight-quarter expiry. | Jason Kelly's **2023** https://jasonkelly.com/2023/02/tqqq-and-volatility-decay/ explicitly says two. **2020** public video describes a different earlier one-skip revision; older 3Sig sources use four. Default to the newer 2023 rule; not pretending no historical variations exist. |
| 100%-up spike | Use daily USD **TQQQ** closes to detect at least 2× the **previous completed calendar quarter** price (provisional reference), during the quarter. Alert owner immediately to review a potential 60/40 reset. | Community mid-quarter discussions https://www.reddit.com/r/KellyLetter/comments/1t4pi9j/9sig_100_spike_reset_trigger/ and https://www.reddit.com/r/KellyLetter/comments/1tv3zt3/hit_my_spike_reset_trigger_at_8718_today/ . **Important:** Kelly Letter uses the actual prior quarterly rebalance execution reference price; exact price may differ from the quarter close, as explained https://www.reddit.com/r/KellyLetter/comments/1td0a4r/100_spike_reset/ . The app therefore raises a WARNING rather than inventing an executable GBP order. |
| Confirmed 60/40 reset | The owner must actually trade; after real fills and updated quotes the owner confirms a new immutable 60/40 baseline. Future target growth is computed from that new baseline. | Workbook AV4 has a REBALANCE conditional; existing verified Wealtharr baseline controls. |
| Notifications | Only action-worthy completed-quarter reviews, newly detected 30 Down, and provisional mid-quarter spike warnings are dispatched. No-trade reviews are saved without alerting. | Wealtharr product choice. Actual delivery requires configured SMTP/Discord and working scheduler. |

## Instrument differences: rule triggers vs holdings

- **US references, USD:** SPY for 30 Down, TQQQ for 100% spike. These are NOT owner trade instructions.
- **UK owner implementation (Trading 212 ISA/GIA, where actually available):** LQQ3.L, WisdomTree 3x daily Nasdaq-100 ETP (equity sleeve); ERNS.L, GBP ultrashort bond ETF (safe sleeve). Initial 60/40 target.
- LQQ3 is a **daily-reset ETP** and ERNS is not AGG. Returns, volatility, fees, currency and bond exposure differ; no backtest of US TQQQ + AGG is presented as the UK owner's earned return.
- Broker ISA eligibility and complex-product access must be verified within the user's actual Trading 212 wrapper, not inferred solely from exchange listing.

## Boundaries still requiring owner verification

1. **Intra-quarter spike exact threshold:** Wealtharr does not yet explicitly label a broker's previous executed rebalance share price as the TQQQ USD spike reference. It can detect a possible 100%-up event from US quarter closes but this is a conservative warning requiring owner verification. A matching-price USD execution reference would require a new recorded field, not an invented fill.
2. **Discretionary spreadsheet overrides:** Manual YES/NO flags, split handling, exceptional forced SELL mode and the workbook's rounding of whole shares are not all implemented as automated owner trade instructions. A particular exceptional review may need manual correction.
3. **Capital depletion and changes to contribution schedules:** Owner-entered contributions are included only if recorded. The £500/month intention is never silently credited to cash; withdrawals reduce actual cash, but the official subscriber-only method for special withdrawals remains unverified.
4. **Delayed market data:** Yahoo is an unofficial source, not an exchange-grade tick stream. Under missing required quotes, do not create a pretend estimate or claim an executable price. Daily-close spike detection may only alert after the exchange closes.
5. **Incomplete strategies/history:** The scheduler handles the last completed quarter for a strategy; it does not reconstruct all intervening historical alerts for a newly imported, years-old portfolio. A complete imported history may require an intentional baseline before any reliable prospective 9Sig cycle.

## Reasons NOT to use arbitrary extra rules

- Do **not** impose an invented 0.5%-of-portfolio "no action" buffer. The workbook does not specify one; very small differences can still produce fractional-unit suggestions subject to account order minimums.
- Do **not** trigger 30 Down on TQQQ; SPY is the published trigger. Do **not** require SPY to be down vs the immediately preceding quarter if it is already 30% below the rolling peak.
- Do **not** force a 60/40 reset solely because eight quarters have passed with NO equity sell; the eight-quarter expiry belongs to a 30 Down window.
- Do **not** mistake an S&P 500 or Nasdaq daily market reference for the listed GBP instrument that must actually be bought or sold.

**Independent risk context:** https://bestfolio.app/blog/kelly-signal-danger shows much deeper drawdowns in long synthetic TQQQ periods than in its 2010-onward actual-data sample. Those synthetic stress results are NOT actual owner returns. 9Sig's leveraged equity sleeve can experience severe or effectively total losses.

## Workbook comparison (DEMO 2010++)

The uploaded **signal strategy simulation sheet v1.07** has only
**DEMO 2010++** values; its main sheet is an empty template. Ordinary
completed-quarter signal lines agree with the demo, and suggested amounts
follow the engine's existing fractional-share precision.

Three **owner-approved, intentional differences** explain exceptional rows:

1. **SPY-only 30 Down:** use the quarter-end SPY close at or below 70%
   of its highest preceding eight SPY quarter closes. Hand-typed
   workbook TQQQ-based 30% flags are not adopted. The TQQQ USD
   reference's running-high drawdown is shown separately from the
   actual GBP growth instrument (LQQ3.L) drawdown where a verified
   historical close series exists. Otherwise that actual-instrument
   value is **unavailable**, not silently inferred from TQQQ.
   Both are **"Information only, doesn't affect the advice"**.
   Crash-quarter results that
   relied on handwritten demo flags intentionally differ from SPY-based
   recommendations.
2. **No sell-half:** P2 (7 May 2010) is a manual mid-quarter
   half-position sale and is not replayed or adopted. During genuine
   SPY 30 Down, skip two would-be sells, review 60/40 on the third
   would-be sell and on eight-quarter expiry; buys continue.
3. **All-in reserve shortage:** when a required growth buy exceeds cash
   plus safe-asset value, sell **all** safe units and buy growth with
   available cash and sale proceeds, floor to normal 0.0001 precision.
   No automatic shortage reset/HOLD, even if 30 Down is active. Show
   **"Your reserve is now empty. Review whether to reset to 60/40."**
   The owner may explicitly choose an **alternative manual 60/40 reset review**
   from the ordinary exhausted-reserve proposal, before recording fills.
   This preserves the original advice and creates a separately audited
   reset proposal using verified quarter-end GBP quotes. No trades occur;
   the original proposal becomes not applicable only by the owner's explicit
   choice. Completion still requires individual actual fills and later
   verified GBP prices through the existing reset-confirmation workflow.
   The 81% throttle, 100%-up spike, third-sell reset and eight-quarter
   expiry stay unchanged.

**Version history:** `nine-sig@2` (`nine_sig.v2.0.0`) keeps the SPY
trigger and no-sell-half policy, adds all-in shortage advice and an
informational growth-reference drawdown. `nine-sig@1`
(`nine_sig.v1.0.0`) remains frozen with its historical reserve
reset/HOLD behaviour. New signals are pinned to the new version.

P1 and P3–P13 are independently replayed from each preceding workbook
row. P2 is deliberately excluded; real-quarter parity is not yet
established from the demo alone.
