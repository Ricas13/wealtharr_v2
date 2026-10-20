# Phase 6 — Performance provenance and refusal policy

History shows real GBP values captured by Wealtharr, but a saved value is not by itself a source-certified investment valuation. Older StrategyValueSnapshot rows retain date, value and funding but omit contemporaneous raw vendor quote units and observation IDs. Phase 6 adds these fields to newly fetched MarketObservation rows, defaulting existing observations to source-UNKNOWN (migration 0018). A market snapshot is certified only by rebuilding its exact recorded cash and holdings at that date from the owner ledger, matching its funding and entire GBP value against source-checked same-day quotes whose raw GBp/GBX/GBP unit and normalized price have been captured. No backfill by inference.

## Permitted outputs

- Recorded history chart: only real saved snapshots and independently reproducible GBP cash-only ledger points, labelled *value*, not *return*. Deposits inflate it; no contributions are inferred.
- Cash-only verified endpoints: replay dated ledger, require zero security quantities and consistent actual GBP cash, matched owner, valid dates, no pending opening reconciliation or revised ledger history. Ledger entry IDs and versions are evidence IDs.
- Source-verified invested endpoints: strictly pinned UK growth/defensive holdings valued with same-day Yahoo MarketObservation source identity, captured original raw GBp/GBX or GBP value, unit scale check and actual dated ledger quantity. The reconstructed total and external funding MUST exactly reproduce the saved snapshot. A current PRICE_HOLD refuses certification. Only a real captured vendor response can set the evidence fields; older rows remain unapproved. This proves GBP valuation data, not account-level ISA eligibility or that a broker trade occurred.
- XIRR: use released evaluate_inception with every real dated deposit and withdrawal and the certified closing amount. Unambiguous actual cashflows only.
- Exact TWR and unit NAV drawdown: use evaluate_period with two verified endpoints and all matching pre/post external-flow boundaries. No fabricated valuations; lacking boundaries means TWR/drawdown unavailable.
- Consolidated Overview: verified value and gain only if *every* real included owner account has a certified snapshot for the same current day. No omission, date mixing or substituting zero.
- ISA lifetime net funding is not a current-tax-year allowance estimate. XIRR and TWR are distinct.

## Refusal

- Do not certify invested snapshots from MarketObservation normalized GBP alone, PriceMark.source Yahoo, catalogue expectations or broker listings. Exact Yahoo raw quote metadata plus a dated owner ledger and a matching saved snapshot are mandatory.
- Do not fill absent dates, interpolate returns, sum individual account XIRRs or use a demonstration workbook as the owner's earned returns.
- The source fetcher now captures provider, raw unit, original raw close, verified conversion and observation ID for NEW accepted rows. Valuation reconstruction uses actual dated holdings and revision version IDs. Never manufacture raw evidence for legacy rows or claim external broker execution evidence from a price alone.

Tests are synthetic accounting scenarios only: tracker/test_v2_phase6.py and browser_tests/test_v2_phase6.py. The versioned financial mathematics are untouched.
