# Methodology note

- **What it is.** A tracker and calculator. Each strategy is a published method whose rules are fixed in checked-in code and versioned; neither users nor the admin can edit them. The rules are applied to the deposits, trades and prices a user records.
- **What it is not.** It does not connect to a broker, place orders, give advice, check suitability, or promise results.
- **Inputs.** The user's dated ledger; closing prices from public feeds, each checked for currency/unit, staleness and sudden jumps before use. If a required input is missing or fails a check, the result is "cannot compute" with zero trades and one plain reason.
- **Instruments.** Exact funds come from an evidence-backed manifest per broker, account type and mapping version. Where a fund differs from the original method, the page says so.
- **Output.** A stored result with status, reason code, proposed amounts, explanation, a "How this was calculated" section and a CSV. Alerts are built from the stored result, not recomputed.
- **Hypothetical history.** The Study tab and homepage comparison replay historical prices through the same rules, are labelled hypothetical, and never value real holdings.
- **Returns.** A simple gain is never called a time-weighted return; where boundaries are inadequate, the figure is shown as unavailable.
- **Credit.** Strategy names belong to their authors; Wealtharr is not affiliated with them.
