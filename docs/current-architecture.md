# Wealtharr v2 — Implemented architecture, October 2026

This is a **current implementation map**, not a substitute for the original [target architecture](architecture.md). Check GitHub PR status and CI for changes not yet merged.

## Runtime
Django 5.2, Python 3.12, PostgreSQL 16, WhiteNoise static files, Gunicorn, Docker Compose and an hourly scheduler. Server-rendered templates with vanilla JS; the owner manually confirms and records all broker activity.

## Services and trust boundaries
- Owner-scoped StrategyInstance / Account bind an immutable strategy version and instrument manifest to a ledger.
- LedgerEntry is the original event, LedgerRevision an append-only correction overlay. Replaying effective entries produces cash, contributions and share quantities, including splits. Cash never arises from theoretical strategy proposals.
- `tracker/strategies/registry.py` resolves a released strategy module; pure strategies are versioned and expose their schedule, required market series, and advisory rules.
- Yahoo observations and price revisions are persisted with raw quote-unit/source evidence where available. PRICE_HOLD blocks action suggestions. Daily hypothetical StudyPrice is entirely separate.
- `v2_history.build_history` independently certifies recorded valuations from source evidence, ledger state and dated FX. Exact cash-flow returns and independent benchmark comparisons must never invent valuation endpoints.
- `notification_routing` chooses opted-in email, Discord, Telegram channels. Delivery receipts and scheduler runs provide bounded retry status; no broker orders are produced.
- `wealtharr.sh` and Compose provide backups, verification and health checks. Off-host encryption requires the owner to configure age recipients and SSH destinations.

## Financial non-negotiables
- No auto trading, silent strategy rule upgrades, instrument substitutions or synthetic real holdings.
- Do not silently value holdings using guessed FX, missing Yahoo bars, dividend history or unsupported corporate actions.
- Do not claim exact 9Sig parity with an owner workbook without supplied independent evidence.
- Keep all portfolio data owner-isolated, trade fills auditable and price-dependent screens visibly fail-closed.

## Open design constraints
The venue-proxy holiday heuristic needs an exchange-aware calendar; historical timezone/date changes need auditable migrations; Yahoo distribution completeness is not guaranteed; precise flow-boundary TWR remains distinct from simplified snapshot charts. Separate encryption keys and database-backed throttle protections are release-gated PRs, not assumed on every deployment.

For chronological decisions, see [the immutable handoff archive](archive/RESUME_through_2026-10-22.md).
