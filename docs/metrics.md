# Section D — universal performance metric contract

As of 2026-10-19. **Strategy-agnostic calculation code**, not a declaration that live portfolio values have been independently reconciled. Implementation: `tracker/core/metrics.py`; historical XIRR helper: `tracker/performance.py`. Test data are intentionally synthetic accounting scenarios, **not a 9Sig backtest**. User-provided broker cashflows and independent spreadsheet comparisons remain separate acceptance work.

## Three different percentages — do not conflate them

| Metric | Definition | Conditions | UI label |
| --- | --- | --- | --- |
| **Cash gain (£)** | Current verified value − opening value − net **external** inflows; inception: current value − cumulative recorded deposits + withdrawals | An approved valuation and complete dated external-funding ledger | `Gain £` |
| **Gain vs net contributions (%)** | Inception gain / (deposits − withdrawals) × 100 | Positive net contributions only; otherwise unavailable. **A descriptive comparison, not a return**; contributions at unequal dates distort it | `Gain / net contributed`, with non-return explanation |
| **XIRR (%)** | Annualised investor money-weighted rate from dated deposits (negative investor cashflow), withdrawals (positive) and verified ending value | Actual flow dates/amounts, approved closing value and a mathematically supported sign-change pattern | `Money-weighted return (XIRR)` |
| **TWR (%)** | Multiply returns between **pre/post external cashflow** valuations, whose security/price evidence is identical across each flow | Approved start/end valuations; every intervening dated external movement bracketed by same-price observations and unambiguous order | `Time-weighted return`, or `Unavailable` |
| **Annualised TWR (%)** | `(1+TWR) ** (365.25/elapsed_days) - 1`, with exact zero-duration refusal and -100% total-loss special case | Verified TWR, positive elapsed duration | `Annualised time-weighted return` |
| **Max drawdown (%)** | Maximum observed peak-to-trough drop on **unitised NAV**, NOT raw portfolio value | Full verified cashflow boundaries and ordered intermediate marks | `Maximum observed drawdown`; sample-bound, not the unknowable intraday peak |
| **Allocation drift** | Actual role share − fixed strategy target share, expressed as percentage points | Exact recorded role holdings and target weights that sum to 1; unknown holdings/cash role cannot be silently omitted | `Allocation drift (pp)` |

A *single* funding deposit and no other external movement supports a conventional inception simple percentage; `InceptionMetrics.simple_cumulative_return` is otherwise deliberately `None`. Never display `gain/net contributed` as a substitute for XIRR or TWR.

### Date basis and numerical policy

- Money and rates use Python `Decimal` (never `float`), with unrounded compounding and high-precision local contexts for return chaining.
- XIRR uses **actual elapsed days / 365** to match Excel XIRR, including leap-year day counts. This differs intentionally from the `365.25` calendar-average convention used for annualising time-weighted performance and some historical secondary helpers.
- Do not round intermediate cash balances, unit NAVs, rates or trade quantities to display pennies. Convert to presentation formatting only in the views (Section E).
- Transfers between strategy assets, buys, sells, dividends and fees are **internal** valuation changes—not external capital injections. Dividends are included in valuations/cash, but not as a second investor deposit.
- Where a period begins **after initial funding**, `PortfolioPeriod.opening` represents the **after-funding** NAV. For inception gain/XIRR, `evaluate_inception` instead takes the full actual deposit/withdrawal history including the first deposit.
- `VerifiedValuation.observation_ids` and `verified=True` are mandatory *input assertions by a future trusted, owner-scoped valuation builder*. They do not independently prove a broker balance or quote source. Never fabricate these IDs to make a metric display.

## Exact cashflow-boundary policy

```text
opening post-funding NAV
  → optional genuinely verified intermediate valuation
  → immediately BEFORE real external deposit/withdrawal
      (same recorded price observation identifiers)
  → immediately AFTER the funding event
      (NAV differs by exactly that signed external amount)
  → ...
  → verified closing NAV
```

The audit includes event IDs, dates, signed GBP funding, before/after values and unit NAV. Both sides of each flow must have identical source-quote evidence; without that, `PeriodMetrics` remains `partial` with cash gain/XIRR when supportable but **no** TWR, annualised TWR or drawdown.

When the flow and closing valuation share a **calendar date**, the end valuation must use the final post-flow boundary's quote evidence; otherwise the intraday ordering is unproven. Identical timestamps/quotes across an internal trade or price change still demand correct provenance from the core replay. A daily price recorded only as a calendar date is not evidence that it occurred before a same-day deposit.

If there are no external flows between approved opening and closing valuations, the endpoint total-return calculation is exact for the *observed endpoints*. **Maximum drawdown remains sample-bounded**: sparse intermediate prices may miss a larger drawdown between observations.

### Result availability

- `cannot_compute` and null numerical values when opening/closing evidence is absent, dates malformed, or flow ledger invalid. No zero-by-default performance.
- `partial` when the cashflow record and endpoints support gain/XIRR but exact TWR boundaries or observation order are absent; never synthesize boundary values.
- `ok` when requested exact boundaries and sampled drawdown can be computed. Even `ok` does **not** imply real Trading 212 reconciliation or 9Sig parity.
- A 100% loss reports `TWR=-1`, `annualised TWR=-1`, `max drawdown=1`; investor XIRR may be mathematically undefined and is shown unavailable.
- Positive, zero and negative investor cashflows are dated ledger events; `gain/net contributed` only has a valid denominator when net capital contributed is **positive**.
- Duplicate event IDs, ambiguous within-day ordering, unknown positions, non-GBP valuations, unverified closes, and unmatched boundary evidence fail closed with a reason code.

## Automated acceptance

`tracker/test_core_metrics.py` covers simple, deposit, withdrawal, irregular funding, same-day sequencing, unitised drawdown, unsupported boundaries, currency failures and allocation drift.

`tracker/test_core_metrics_integrity.py` verifies Excel ACT/365 XIRR, invalid input refusals, closing-day flow ordering, full loss, zero-gain irregular flows, descriptive gain percentage and missing/unknown roles.

To check on the current branch: `python manage.py test tracker.test_core_metrics tracker.test_core_metrics_integrity`, followed by the full CI suite. Do not infer green status from queued workflows. Section E will connect **only** these verified contract outputs to the Today and History UI; don't add strategy-specific hardcoding to core metrics.

**Independent numeric verification:** When genuine broker data exists, compare XIRR to Excel `XIRR` with the same exact dates/cashflows; compare gain to broker value minus signed external cash, TWR to an independently unitised same-day cashflow ledger, and drawdown to the available recorded unit NAV series. Never compare the existing synthetic test fixture amounts as an investment history.
