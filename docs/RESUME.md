# Wealtharr v2 — Current engineering handoff
Updated 22 October 2026. Read this file first; the historical, chronological build notes are archived at [docs/archive/RESUME_through_2026-10-22.md](archive/RESUME_through_2026-10-22.md).

## What is shipped on main
- Django 5.2 server-rendered, owner-scoped PostgreSQL investment tracker with a mobile-first dark UI. No brokerage API connections, automatic trade execution, subscriptions or public customer registration.
- Versioned strategies: 9Sig, 3Sig, HFEA, All Seasons, Golden Butterfly, Permanent Portfolio, Dual Momentum, 3× Nasdaq Trend + DCA ([rules](nasdaq-trend-dca.md)). UK, USD and EUR instrument mappings remain explicitly pinned and are not assumed equivalent.
- Onboard an owner-reported portfolio; record real deposits, trades, fees, distributions and corporate share splits. Only explicitly owner-confirmed fills complete action records. Rules compute advisory reviews, not broker orders.
- Yahoo chart snapshots and study history are distinct inputs. Quote-unit validation, price holds and provenance are required for certified historical valuations. Email, Discord and Telegram notification transports are available.
- Homepage comparison and strategy Study are hypothetical with disclosed proxy / execution-model caveats. No fabricated market quotations or broker records.

## Current operational status
- Latest merged financial changes: PR #58 (certified XIRR, period-end gap guard, fill acceptance) and PR #59 (splits, distribution events, exchange-time dating, Study next-close option).
- PR #60 adds verified PostgreSQL pre-update backups, opt-in age-encrypted remote export, and PostgreSQL CI. New update helper cannot protect the *first* upgrade from an earlier helper; take a verified manual dump beforehand.
- Other branch work is tracked by its GitHub PR description and CI, not assumed shipped: performance batching, stricter financial series gating, shared database throttling, and opt-in dedicated credential encryption.
- Deployments are operator-run on the Oracle host only. A GitHub merge is not proof of live deployment.
- External Yahoo dividend/timezone behaviours are not fully exercised with live provider credentials. UK market-data rights and independent 9Sig workbook parity need separate evidence.

## Primary code map
- `tracker/v2_urls.py`, `tracker/v2_today.py`: routed owner experience and next-action surface.
- `tracker/v2_ledger_core.py`, `tracker/services.py`: immutable revision projection and financial replay.
- `tracker/strategies/registry.py`, `tracker/strategies/*`: pinned released rules, trading line mappings and per-strategy workflows.
- `tracker/market_data.py`, `tracker/core/prices.py`: quote ingestion, normalized units and quality gates.
- `tracker/v2_history.py`, `tracker/core/metrics.py`: source evidence and return calculations.
- `tracker/notification_scheduler.py`, `tracker/notification_routing.py`: alerts and retries.
- `compose.yml`, `wealtharr.sh`, `.github/workflows/test.yml`: deployment and verification.

## Financial and release invariants
1. Ledger and owner permissions stay authoritative. Never invent a fill, deposit, historical close, exchange rate, broker eligibility or corporate action.
2. New strategy behaviour requires a new version. Do not silently change a pinned investment rule, quote unit or strategy mapping.
3. Portfolio returns require authentic dated evidence. Snapshots are records until independently certified; an unverified value never becomes a precise return.
4. No automated brokerage access and no unapproved cross-currency summation. Nothing should be presented as an executable order.
5. Unit tests, owner-isolation tests, financial parity tests, PostgreSQL schema checks and browser journeys should pass before merge. Record known gaps in PRs.
6. Preserve production data and secrets across deployments. Use isolated verified recovery tests and never wipe the owner database in CI or a routine update.

## Current risks to revisit
- Holiday/quarter-end market-data validation still uses a venue-proxy heuristic, not a verified exchange calendar.
- A non-empty dividend-event table doesn't prove that all distributions were received.
- Legacy Yahoo timezone-shifted observations require a careful auditable correction plan; no silent backfill.
- Historical dashboard TWR for irregular cash flows still needs exact event-boundary audit.
- Off-server backup destination, full offsite restore drill and optional dedicated encryption-key transition are operator actions.

## References
- [Engineering constraints](../CLAUDE.md)
- [Original architecture design contract](architecture.md) (historical target, not present implementation)
- [Owner evidence and actions](OWNER_TODO.md)
- [Financial QA](FINANCIAL_QA.md)
- [Archived detailed implementation chronology](archive/RESUME_through_2026-10-22.md)
