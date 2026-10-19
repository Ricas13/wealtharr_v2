# Section F — real UK ISA onboarding and Trading 212 CSV import

Research / code-review date: **2026-10-19**. Source: the current repo and publicly available HMRC/Trading 212 help information; not proof of this owner's account eligibility or broker reconciliation.

## Supported new user journey

1. Sign in; open `/cockpit/start/`. The **only new flow** fixes UK, Trading 212, GBP, Stocks & Shares ISA and versioned 9Sig. No user-selected stock symbols or fund substitutions.
2. Review LQQ3 (GBX line, **IE00BLRPRL42**) and ERNS (GBP line, **IE00BCRY6441**), including the explicit **personal ISA access not verified** caveat. Versioned exact instruments and evidence snapshots are stored with the new strategy. Altering the released mapping or the stored snapshot blocks the runtime resolver; no silent remapping.
3. Select **starting from actual cash** or **existing account with imported history**.
   - Fresh: optional already-received GBP cash **and** its real receipt date; blank means no deposit. The £1,000 example and £500/month reminder are **not fictional deposits**. No BUY/SELL transactions or positions are created.
   - Existing: creates an *empty tracked strategy*, then navigates to the original CSV history preview. Opening cash/positions cannot be typed as a guess.
   - A matching existing, unused Trading 212 ISA Account can be reused; an account already holding strategies or with incompatible country/broker/account type fails transactionally without modifying it.
4. Set monthly reminder amount (default £500; £0 allowed) and optional email/Discord alert selection. Actual delivery configuration remains an explicit Settings task (Section H makes routes and retries richer).
5. Today shows one next action; owner records actual deposits with their actual dates. Trading is always done **outside Wealtharr**; Section G is the guided actual-fill improvement.

The retired generic `/app/strategies/new/` and legacy `start_real_9sig` entrypoints redirect to the supported setup without creating a GIA or arbitrary rule set. Older existing portfolios and legacy test fixtures are not erased or silently converted.

## 2026/27 UK ISA warning (advisory only)

- **Evidence:** HMRC [ISA overview](https://www.gov.uk/individual-savings-accounts) and [subscription rules](https://www.gov.uk/guidance/manage-isa-subscriptions-for-your-investors) confirm an **overall £20,000 allowance** for 2026/27, from **6 April 2026 through 5 April 2027**. It is aggregated across ISAs, *not* per Stocks ISA or per broker.
- `tracker/core/isa_allowance.py` counts only **recorded DEPOSIT events** in the matching tax year; it does **not** deduct withdrawals (flexible-ISA replacement is not inferable from simple cashflow rows). Optional other-ISA subscriptions are provided **only for the immediate estimate**; they are not persisted as this strategy's funding.
- Above 80% of the total or at/over the limit, the owner receives a warning **after** actual cash is safely recorded. This is deliberately **not a block**, statutory approval or allowance certificate.
- No claim is possible about payments into other ISAs, HMRC adjustments, genuine ISA-to-ISA transfers, or flexible-ISA replacements. If a deposit belongs to an unsupported tax year, **no numeric statutory estimate is made** and the owner is directed to HMRC.
- Future changes to cash ISA limits are a different policy from the overall Stocks & Shares ISA cap. No ages, other tax wrappers or private tax data are stored in Wealtharr for this estimate.

## Trading 212 CSV — exact supported workflow

1. In Trading 212 select **History → Export**, including *Orders*, *Transactions* and *Dividends* for the relevant real ISA period. [Trading 212 export help](https://helpcentre.trading212.com/hc/en-us/articles/360016898917-Can-I-export-the-trading-data-from-my-account). Export a complete relevant date range; do not edit raw ID, columns or currencies.
2. Upload UTF-8(.BOM allowed) CSV, maximum 4 MB / 15,000 rows. Preview is read-only and shows new rows, duplicates, row-specific errors and a breakdown of up to 100 candidate rows.
3. Every row must have supported action, actual UTC-comparable date, supported GBP cash or GBP/GBX trade price, a **matching exact ISIN** for trades, and fees whose GBP currency is evidenced. A missing/invalid date, mismatched currency/ISIN, unsupported action/corporate event or excess CSV values/duplicate headers produces errors, not heuristics.
4. Fix **all** preview errors before importing anything. Exact repeats are skipped; manual transaction overlaps and conflicting reused broker IDs are held for reconciliation. A preview cannot be used to import a changed file.
5. Confirm by re-uploading the **same file bytes** in the same authenticated session. Within a locked atomic transaction, the importer rechecks the ledger and rows, and appends the validated entries + idempotent event fingerprints. If a later row fails (e.g. negative cash/positions or out-of-order history), **the entire batch is rolled back**.
6. Compare final ledger quantities, GBP cash, all fees, dividends, missing or extra events against the broker account. Wealtharr **never connects to Trading 212, submits orders or certifies that a CSV is complete**. Unreconciled rows intentionally stop executable 9Sig instructions.

## Acceptance and outstanding owner evidence

- Automated tests: `tracker/test_section_f_isa.py` (6 April boundary, tracked vs other-ISA subscriptions, warnings and real-cash setup), `tracker/test_cockpit.py` (preview, changed-file refusal, atomic import, ISIN, FX and ID conflicts), plus all existing owner-scoped fixture tests.
- Run the complete GitHub Actions Django test suite/migration check/collectstatic. Skip only the deliberately unfilled **real Jason Kelly workbook parity fixture**; never invent it.
- **Still open:** real redacted Trading 212 export with broker-specific columns, full money/positions reconciliation, actual owner trading permissions and ISA product access, real 9Sig workbook parity, and browser/mobile interaction checks. These are also in `docs/OWNER_TODO.md`.
- Section G must preserve immutable actual fills, explicit owner confirmation and reset-after-verified-post-fill evidence. Section F creates **no new trading workflow**.
