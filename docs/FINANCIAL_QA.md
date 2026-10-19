# Finance acceptance gates before real-money reliance

**Spreadsheet / engine**
1. For at least six observed quarter-ends (including one contribution, a no-trade, 30 Down, an 81% throttled buy and a reset), compare Wealtharr's signal-line pounds, proposed share counts and cash against the owner's 9Sig workbook, penny/share-tolerance specified in advance.
2. Confirm 9% quarterly compounding and *half of within-quarter new deposits entering only at quarter close*.
3. Confirm TQQQ spike reference: current code uses calendar-quarter close as a **warning**; historical actual execution rebalance price may differ. Never turn this into an automatic GBP reset without evidence.
4. Confirm SPY rolling eight-quarter 30 Down; two skipped sell notices, third reset and eight-quarter expiry; do not count unrelated HOLD signals.
5. Confirm reserve throttle is applied only on large buys when 9Sig wants >=90% reserves and uses 81% of total reserve, not 81% of cash alone.
6. Historical quarterly close is last verified prior trading close (holidays); ensure both GBP sleeves and USD references are consistent with the workbook, no implicit FX, and the correct GBP/GBX divisor.
7. UK LQQ3 daily-reset ETP differs from TQQQ US equity fund, ERNS ultrashort bonds differ from AGG. Confirm actual ISA availability, product permissions and exact ISIN at Trading 212.

**Accounting / import**
1. Use a COPY of the real Trading 212 export, verify every header and charge currency. Import preview first; reconcile cash and shares to the broker before trusting signals.
2. Dry-run duplicates twice; import repeats should add zero new rows. Manually recorded overlaps must error rather than double-count.
3. Unmodelled holding, unsupported action (split/FX/interest), missing price, stale Yahoo quote, wrong price currency, future transaction and negative cash must all block.
4. Simulate database failure during the second fill: neither the first fill nor baseline/execution marker must survive rollback.
5. Verify ordinary rebalance produces no new StrategyBaseline; verified 60/40 reset does produce exactly one.
6. Verify no baseline is written until prices dated after fills are available, even if the user clicked 'I did this'.
7. Compare gain £, XIRR and TWR results with independent dated-cashflow spreadsheet; withdrawals and dates inside valuation gaps must not be presented as exact TWR.

**Security / notifications**
1. Test unauthenticated and second-owner access to every endpoint; only staff may change global transport secrets.
2. Verify private webhook URL/SMTP password never appears in templates, logs or exception messages.
3. Test alert idempotency on repeated scheduler runs and simulate lost network acknowledgements (at-least-once delivery).
4. Suppress old immediate quarterly dispatch before enabling the new digest scheduler; continue urgent SPIKE_WATCH and THIRTY_DOWN notices.
5. Freeze all automatic broker writing: new API key must be **read-only**, API client must expose only GET methods and keep order endpoints inaccessible.

**Known limitations in this reference patch**
- Existing-holdings onboarding collects the mode but requires a full historical CSV in the following screen; it does not create fictional opening purchases.
- Verified post-trade closes may arrive next day. The rebalance confirmation intentionally refuses/rolls back until they arrive; this is safer than silently anchoring unverified figures, but costs an extra visit.
- A new deposit after an issued quarterly signal makes the saved instruction potentially outdated. It is blocked; a future enhancement should safely rederive the instruction without changing the completed-quarter calculation or using future prices.
- TWR and drawdown show 'unavailable' when available snapshots lack exact flow-aligned valuation boundaries. We do not call an approximation exact.
- No actual broker CSV sample or live Django check was available, so CSV parsing/wiring should be checked before production writes.
