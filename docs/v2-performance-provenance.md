# Phase 6 — Performance provenance and refusal policy

History shows real GBP values captured by Wealtharr, but a saved value is not by itself a source-certified investment valuation. Older StrategyValueSnapshot rows retain date, value and funding but omit captured raw vendor quote units and their contemporaneous observation IDs.

## Permitted outputs

- Recorded history chart: only real saved snapshots and independently reproducible GBP cash-only ledger points, labelled *value*, not *return*. Deposits inflate it; no contributions are inferred.
- Cash-only verified endpoints: replay dated ledger, require zero security quantities and consistent actual GBP cash, matched owner, valid dates, no pending opening reconciliation or revised ledger history. Ledger entry IDs and versions are evidence IDs.
- XIRR: use released evaluate_inception with every real dated deposit and withdrawal and the certified closing amount. Unambiguous actual cashflows only.
- Exact TWR and unit NAV drawdown: use evaluate_period with two verified endpoints and all matching pre/post external-flow boundaries. No fabricated valuations; lacking boundaries means TWR/drawdown unavailable.
- Consolidated Overview: verified value and gain only if *every* real included owner account has a certified snapshot for the same current day. No omission, date mixing or substituting zero.
- ISA lifetime net funding is not a current-tax-year allowance estimate. XIRR and TWR are distinct.

## Refusal

- Do not certify historical invested snapshots using only MarketObservation.normalized GBP, PriceMark.source Yahoo, catalogue expected GBX raw unit or a broker listing. These do not prove the original raw quote-unit/source provenance.
- Do not fill absent dates, interpolate returns, sum individual account XIRRs or use a demonstration workbook as the owner's earned returns.
- Future source capture must store original provider, raw unit/price, normalization, observation IDs, dated holdings and contemporaneous ledger checkpoint. Do not retro-certify historic rows without independent evidence.

Tests are synthetic accounting scenarios only: tracker/test_v2_phase6.py and browser_tests/test_v2_phase6.py. The versioned financial mathematics are untouched.
