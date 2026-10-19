# Section B — UK / Trading 212 Stocks & Shares ISA instrument evidence

**Research reviewed:** 2026-10-19 (Europe/London). **Status: issuer identity/UK eligibility verified; owner-specific Trading 212 Stocks ISA dealing permission and Yahoo quote metadata NOT verified.** This document is one-off research used to define the **fixed code catalogue** in `tracker/instrument_mapping.py`; no broker connection, runtime instrument selection or user-entered ticker is allowed.

**Selection:** retain **LQQ3.L** as `growth` and **ERNS.L** as `safe` at version `2026-10-19.1` for initial workbook/backhistory comparability. These are reasonable implementation candidates, **not exact TQQQ/AGG substitutes and not proven financially optimal**. Neither line may be declared personally ISA-tradeable until the owner checks it in their own Trading 212 Stocks ISA.

## Fixed selected mapping, explicit units and evidence

Curated map key is `(strategy_key, role, country, platform, account_type, mapping_version)`; there are exactly two reviewed rows in the **new ISA catalogue**. The old GIA entry in the legacy `SUPPORTED_PROFILES` table remains only for existing runtime/tests until Section F removes old setup routes; it is **not** supported in new onboarding.

| Field | Growth role | Safe role |
| --- | --- | --- |
| Strategy | `nasdaq-quarterly-target` | `nasdaq-quarterly-target` |
| Mapping version | `2026-10-19.1` | `2026-10-19.1` |
| Country / platform / account | GB / TRADING212 / ISA | GB / TRADING212 / ISA |
| Full name | WisdomTree NASDAQ 100 3x Daily Leveraged | iShares £ Ultrashort Bond UCITS ETF (Dist) |
| Issuer | WisdomTree Multi Asset Issuer PLC | iShares IV plc (BlackRock) |
| Role weight in 9Sig v1 | 60% **initial/reset** target, NOT constant quarterly allocation | 40% **initial/reset** target |
| ISIN | **IE00BLRPRL42** | **IE00BCRY6441** |
| Exchange trading line | **LQQ3**, LSE, GBX, SEDOL BKSB2C0 | **ERNS**, LSE, GBP, SEDOL BCRY644 |
| Broker catalogue code | `LQQ3.GB` (Invest page) | `ERNS.GB` (Invest page) |
| Price feed lookup | `LQQ3.L` | `ERNS.L` |
| Exchange raw quote units | **GBX / pence** | **GBP / pounds** |
| GBP normalisation | raw GBX × Decimal('0.01') | raw GBP × Decimal('1') |
| Trading settlement/base | GBP trading line; underlying/base currency USD, **not currency hedged** | GBP trading and share-class currency |
| Product | Fully collateralised, swap-backed **debt-security ETP**; **not a UCITS ETF** | Physically sampled, unleveraged **UCITS ETF** |
| Strategy exposure | Three times **DAILY** Nasdaq-100 net total return, before funding costs/fees | GBP ultrashort investment-grade corporate and quasi-government bonds |
| Reset / income | **Daily leverage reset**; issuer intraday restrike 17%, overnight-gap threshold 25%. No cash income expected | No leverage; **quarterly distributing** share class. Import/record actual dividends |
| Issuer ISA classification | **Eligible** per issuer | **Yes** per issuer |
| Trading 212 public Invest page | **Found** | **Found** |
| Trading 212 Stocks ISA dealing for actual owner | **NOT VERIFIED** | **NOT VERIFIED** |
| Complex-product test/fractional availability | **NOT VERIFIED** | **NOT VERIFIED** (fractionals/minimums) |
| Yahoo live raw unit and full series availability | **NOT VERIFIED** independently in this research | **NOT VERIFIED** independently in this research |
| Evidence last reviewed | 2026-10-19 | 2026-10-19 |

### Primary evidence — growth

- WisdomTree issuer (ISIN, listing table, issuer, ISA status, product structure, fees, daily reset and restrike): https://www.wisdomtree.eu/sv-se/etps/equities/wisdomtree-nasdaq-100-3x-daily-leveraged
- LSE GBX trading line: https://www.londonstockexchange.com/stock/LQQ3/wisdomtree/company-page
- Trading 212 **Invest**, **not ISA-proof**: https://www.trading212.com/trading-instruments/invest/LQQ3.GB
- Issuer identifies this as a collateralised swap-backed **ETP**, not a UCITS fund. Its 3x objective is **daily**, not three times the index over quarters. Non-GBP price exposure remains even though the dealing quote is GBX. Extreme events/restrikes can substantially change its performance.

### Primary evidence — safe

- iShares/BlackRock issuer: https://www.ishares.com/uk/individual/en/products/258120/ishares-ultrashort-bond-ucits-etf
- Issuer lists LSE `ERNS`, **GBP** and RIC `ERNS.L`, ISIN `IE00BCRY6441`, ISA Eligibility **Yes**, UCITS **Yes**, distribution **Quarterly**, TER **0.09%**, and ultrashort investment-grade GBP debt.
- Trading 212 **Invest**, **not ISA-proof**: https://www.trading212.com/trading-instruments/invest/ERNS.GB
- The fund holds debt with credit, interest-rate and liquidity risk; it is **not insured cash**, not a UK government-only product and **not AGG**. Ex-dividend price drops must not be mistaken for losses without recording actual dividends.

### Regulatory and platform boundaries

- Trading 212 says instrument offerings depend on account type: https://helpcentre.trading212.com/hc/en-us/articles/11717160183197-What-trading-instruments-does-Trading-212-offer
- Trading 212 says leveraged/complex ETP instruments can require an appropriateness assessment, including UK Stocks ISA: https://helpcentre.trading212.com/hc/en-us/articles/10397198010525-Appropriateness-assessment-test-in-Invest-ISA-SIPP
- HMRC describes eligible Stocks & Shares ISA securities: https://www.gov.uk/guidance/stocks-and-shares-investments-for-isa-managers
- **No public Trading 212 Invest instrument page is evidence of permission to BUY that exact security in an individual Stocks ISA.** Direct ISA instrument URLs were not accessible through research. Do not infer eligibility from an Invest landing page or general UK tax categories.

## Alternatives researched — **not runtime substitutions**

| Role | Candidate | Identification/evidence | Why not selected for mapping v1 |
| --- | --- | --- | --- |
| Growth | **Leverage Shares 3x Long Nasdaq 100**, LSE GBX line `3QQQ` | Trading 212 Invest `3QQQ.GB` / **XS2472197065**. https://www.trading212.com/trading-instruments/invest/3QQQ.GB ; LSE matching ISIN on other currency lines https://www.londonstockexchange.com/stock/QQL3/leverage-shares/company-page | Plausible 3x daily Nasdaq exposure and substantially lower nominal GBX share quote than LQQ3; **distinct provider, funding/reset/collateral, fees, tracking and history**. Need exact GBX issuer listing/appropriate personal ISA proof, spread and execution comparison before any switch. Not equal to WisdomTree |
| Growth | **WisdomTree QQQ3**, LSE **USD** line of same ISIN as LQQ3 | WisdomTree's listing table `QQQ3.L`, USD; **IE00BLRPRL42** | Not a separate 9Sig alternative product; an alternative **USD trading line** of the same security. Adds settlement FX complexity for GBP-only tracker; do not conflate with Leverage Shares' **GBX** `3QQQ` |
| Safe | **iShares £ Ultrashort Bond UCITS ETF (Acc)**, LSE `ERAN`, GBP | iShares **IE000V5JWFM0**, new accumulation class from Jan 2026, ISA eligible: https://www.ishares.com/uk/individual/en/products/347803/ ; Trading 212 Invest: https://www.trading212.com/trading-instruments/invest/ERAN.GB | **Potentially easier** for a new portfolio because no distribution ledger entry, smaller nominal unit; but has **short live price history** and is a *different ISIN*. Current ERNS historical parity/backfilling is easier. No personal ISA access proof; explicit mapping version switch required |
| Safe | **Amundi Smart Overnight Return UCITS ETF GBP Hedged Acc**, LSE `CSH2` | Amundi `CSH2 LN`, **LU1230136894**, GBP-hedged overnight-return style: https://www.amundietf.co.uk/en/professional/etf-products/search | A cash-rate-like rather than bond-index exposure, synthetic/counterparty and tracking risks; **Trading 212 Stocks ISA listing, live quote unit, minimum units and account access unverified**. Not a drop-in replacement for ERNS or AGG |
| Safe | Uninvested GBP ISA cash | Trading 212 cash balance is an account balance, not an ETF ISIN | Broker cash yield/eligibility/settlement semantics differ; would require a **new strategy version and explicitly different safe role**, never silently mapped to ERNS |

**Decision quality:** For first implementation focused on matching the current spreadsheet and historical calculations, **LQQ3 + ERNS is defensible**. For a new owner with small initial capital and wanting minimum manual work, **3QQQ and especially accumulating ERAN deserve consideration**; neither is automatically better, and changing the selected lines requires a code release/version. It would be misleading to call any one combination the absolute "best" before verifying ISA access, fractional dealing, order size, bid/ask spreads, funding costs and full historical tracking differences.

## Owner-only manual verification, once before relying on recommendations

Within your actual **Trading 212 Stocks & Shares ISA** (not Invest/CFD), without giving Wealtharr any API access:
1. Search **ISIN** `IE00BLRPRL42`; confirm **LQQ3** is the **LSE GBX** line, buy button availability, access to leveraged ETPs and any knowledge assessment. Do not mistake QQQ3 USD or Leverage Shares 3QQQ for LQQ3.
2. Search **ISIN** `IE00BCRY6441`; confirm **ERNS** GBP **distributing** line and buy availability. Check whether dividend cash appears in Trading 212 export.
3. Look at a **non-submitted** order preview for both instruments: verify exact quote units, minimum order, fractional shares, spread and expected fees. With £1,000 initial portfolio and 60/40 start, whole-share size for LQQ3 may cause significant drift.
4. If a line is absent, restricted or unsuited to £1,000, mark the pairing **NOT APPROVED**; compare alternatives, then release a **new versioned mapping**. Never pick the next-nearest product at runtime.
5. Capture the permission check date and result as an owner-entered account capability/acknowledgement in **Section F**. Absence of this remains visible as `owner_confirmation_required` in the code catalogue; current legacy views do **not** enforce that gate yet.

## What Section C must independently verify

1. Fetch raw Yahoo metadata on a real date for `LQQ3.L` and `ERNS.L`; confirm the reported quote currency (GBp/GBX vs GBP) and validate the ×0.01 conversion against LSE/broker **same-date** closes. **Never apply two factors** or assume every `.L` ticker is pence.
2. Keep `SPY` and `TQQQ` as **USD reference-only** series, never mapped to UK holdings or converted to model trades.
3. Verify history integrity across any corporate actions, especially leveraged ETP **splits/consolidations/restrikes** and ERNS **distributions**. Yahoo split-adjusted series must be reconciled with actual holdings; automatic adjusted historical prices must not silently rewrite actual broker fills.
4. Validate issuer/market-data quote provenance, dates, stale/hold guards and mismatched currency. Add a second independent data source; two Yahoo hosts are not independent sources.
5. Compare the owner's quarter-end workbook as-of dates and GBP unit values; do not present UK LQQ3/ERNS returns as historic US TQQQ/AGG strategy returns.

## Implementation notes / nonclaims

- `tracker/instrument_mapping.py` now includes explicit `role`, `MAPPING_VERSION`, `verified_on`, issuer/broker evidence, reset and dividend characteristics, quote units and GBP normalization factors. Immutable `CURATED_ISA_ROLE_MAP` contains two roles and **ISA only**; `resolve_curated_isa_role()` rejects unsupported role/country/broker/account/version combinations.
- Existing `resolve_instruments()`, old `SUPPORTED_PROFILES` (including old GIA calls), `for_strategy()`, signals, fields and setup remain for compatibility until later implementation sections. New core onboarding must use the ISA catalogue, pin versions and require account permission acknowledgement, **not** accept a public Invest page as proof.
- `yahoo_price_unit_confirmed=False` and `broker_isa_confirmed=False` are intentionally explicit on both records. `verified_on` means **issuer and Invest evidence reviewed**, not complete personally permitted execution.
- No broker link, live brokerage check or trade has been attempted. Research cannot independently verify your personal trading permission, whole/fractional fill support, live buy/sell spread, or unseen dividends. Do not overstate those limits.
