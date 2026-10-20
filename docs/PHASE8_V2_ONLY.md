# Phase 8 — v2-only entry and legacy data audit

## In-place link (no database reset)

The PR #11 cockpit and v2 already use Account, StrategyInstance, LedgerEntry, StrategySignal, StrategyBaseline and StrategyValueSnapshot. Migration 0021 keeps ALL existing primary keys and foreign keys. It assigns a v2 strategy key only when UK/GBP, Trading 212 ISA and both exact instrument symbols are already proved in the database. Everything else remains listed via shared owner-scoped models, with tracking_only retained or enabled. Broker permissions, true starting date, original price-unit evidence, and current approved quantitative version cannot safely be inferred. The migration deliberately leaves the old strategy/mapping versions unpinned; no claim of 9Sig parity or executable advice is made.

## Bookmarks and safety

Root and post-login select sole owner strategy Today, or Overview when zero or multiple strategies exist. Legacy cockpit/app GET bookmarks permanently redirect, owner-scoped, to Today, History, Ledger, Strategy, Settings or Overview. Deprecated financial POST routes reject the request with 405 rather than inadvertently replaying trades. Authenticator, Turnstile, health and staff admin remain protected.

## Approved prototype: remaining static differences

Playwright captures login, overview, Today, History, Ledger, Strategy, Breakdown, add-strategy, catalogue and settings at 1440px and 390px. It asserts width/height chrome geometry against docs/design/app_v2.html and no horizontal overflow or other-owner information. The prototype has synthetic performance figures and no authenticated login; no pixel parity with actual investments is claimed.

Known differences: v2 standard panel radius 8px versus prototype 14px; v2 accent #6ea8fe versus prototype desaturated cyan #9aa8bd. Login/TOTP/Turnstile and data-quality refusal states have no equivalent static prototype. Actual screenshots should be visually approved after CI artifacts are captured.

## Important remaining dependency

The v2 Today read-only adapter still imports _price_status, _last_action and _next_end from tracker.cockpit. Removing that module would require extracting its logic and independently proving financial parity, which is explicitly outside layout scope. Its old endpoints are no longer routed. Some existing financial safeguard tests POST legacy cockpit handlers and expect legacy HTML. Do not delete or skip those tests before equivalent v2-endpoint cases are established. If these tests fail in CI, the draft PR is **not merge-ready**; a separate safe test-porting change is necessary.

Baseline (merged main): 395 Django tests, 7 expected skips, and 27 Chromium tests. No local Django is installed, so before/after local execution counts are unavailable. CI is checked once on the draft head; queued is not a failure but not proof of success. No production DB reset, migration execution or deployment was performed.
