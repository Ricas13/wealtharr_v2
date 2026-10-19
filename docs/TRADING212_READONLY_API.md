# Optional Trading 212 read-only integration (decision note)

**Recommendation:** ship the CSV importer first. Add a broker sync only after a real export has validated the instrument / fee / FX mapping. Do not request trading permissions, and never send broker orders from Wealtharr.

## Why add it later?

Pros: fills, dividends, deposits and position reconciliation without retyping; better user experience after setup; automatic detection of differences between broker state and the append-only Wealtharr ledger.

Cons: API availability and scopes vary by Trading 212 account; corporate actions, corrections, fees and FX need reconciliation; historical page sizes/rate limits and changes can create duplicates; no guarantee the same timeline/fields as CSV exports; encrypted credentials must be maintained and rotated.

## Security design

- Owner supplies a read-only API key with only documented GET/history/position scopes for supported ISA/Invest accounts. Never store a trading-scope key.
- Encrypt at rest using the existing integration cipher; keep it out of templates, database dumps shared externally, URL parameters and logs. Consider a separate rotated encryption key rather than deriving everything from SECRET_KEY.
- Server-side outbound client must maintain an explicit GET-only allowlist of endpoint paths and `api.trading212.com` host; reject redirects to unknown hosts and limit size/timeouts.
- Sync results land in a preview/reconciliation queue. Nothing modifies the immutable ledger until the owner approves new events; reuse BrokerImportRow-style stable fingerprints with broker account ID and instrument ISIN.
- One Trading 212 account only. Verify account/currency/ISIN before accepting data. Fail closed on splits/mergers, reversals, multi-currency events or mismatched positions.
- Do not infer that a product is ISA-eligible because it appears in a general broker instrument catalogue.

## Rollout gate

Obtain a real read-only API example, compare one month of deposits/buys/sells/fees/dividends with the same CSV export and actual GBP broker cash & share balances; include fixtures and regression tests. Keep CSV as a reliable fallback. API keys should be revocable from Trading 212.
