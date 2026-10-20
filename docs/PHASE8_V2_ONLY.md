## Continuation: reset choice and TOTP redirect

- Reused existing `choose_manual_reserve_reset` service from `tracker.cockpit_manual_reset` inside v2 guided review. Only current versioned exhausted-reserve notices can offer the owner-elected alternative; the original all-in proposal stays intact. A separate review never creates trades or a baseline; actual fills and verified later GBP closes remain required. `tracker/test_manual_reserve_reset.py` now exercises this v2 path without relaxing financial assertions.
- Fixed remaining successful TOTP challenge redirects to use `preferred_url(request.user)` rather than `/cockpit/`. Original tests uncovered a real redirect regression.
- Interim CI on `b9583ea` (before later porting): 385 Django tests, 52 failures and 2 errors, seven skipped, versus 72 failures and three errors initially. **Not a final pass**. More old test and browser journeys still require porting. No live database was changed.

---

# Phase 8 — continuation: v2-only owner tests and saved-notice feedback (20 October 2026)

- Ported legacy safety assertions to v2 in `tracker/test_cockpit.py` (partial fills, verified-close baseline, rollback, duplicate CSV import, preview gate, actual cash), `tracker/test_g_single_fill.py` (idempotency, partial and final completion), `tracker/test_owner_totp.py`, `tracker/test_i_security_and_recovery.py`, `tracker/test_real_9sig_setup.py`, `tracker/test_layout_cleanup.py`, `tracker/test_h_notification_matrix.py`, `tracker/test_signal_review.py`, and `tracker/tests.py`. These remain regression tests: none was skipped merely to clear CI.
- Restored saved-signal `CONFIRMED` / `FALSE_POSITIVE` / `NOT_APPLICABLE` **assessment** via `POST /s/<id>/notice/<signal_id>/feedback/`. Requires owner scope and a reason for false positives; cannot modify a signal with actual linked fills or completed execution. This does not confirm broker orders. Today offers a collapsed feedback form.
- Fixed deprecated `/app/strategies/<id>/...` owner scope before POST/405. Other people's strategies return 404.
- Screenshot suite on head `9b6481f` **passed** (2 tests, 1440px and 390px capture); GitHub Actions uploaded its screenshot artifact. This is a geometry/overflow comparison, not exact visual parity.
- Baseline remains 395 Django tests (7 skips) and 27 Chromium tests on merged main. Before the continuation, head `9b6481f` CI ran 385 Django (72 failures, 3 errors, 7 skips) and 29 Chromium (1 failure, 13 errors); after the continuation, new suite changes are **not yet confirmed**. CI must pass before merging. Local before/after execution is unavailable: no local GitHub checkout, Django installation or complete test runner in this execution environment.
- Many old single-page ISA, legacy signal controls, manual 60/40 reserve reset and old browser journeys still target retired UI. Their safeguards must be ported without changing calculations before this PR can be merge-ready. No production data was touched or deployed.

---

# Phase 8 — v2-only entry and legacy data audit

## In-place link (no database reset)

The PR #11 cockpit and v2 already use Account, StrategyInstance, LedgerEntry, StrategySignal, StrategyBaseline and StrategyValueSnapshot. Migration 0021 keeps ALL existing primary keys and foreign keys. It assigns a v2 strategy key only when UK/GBP, Trading 212 ISA and both exact instrument symbols are already proved in the database. Everything else remains listed via shared owner-scoped models, with tracking_only retained or enabled. Broker permissions, true starting date, original price-unit evidence, and current approved quantitative version cannot safely be inferred. The migration deliberately leaves the old strategy/mapping versions unpinned; no claim of 9Sig parity or executable advice is made.

## Bookmarks and safety

Root and post-login select sole owner strategy Today, or Overview when zero or multiple strategies exist. Legacy cockpit/app GET bookmarks permanently redirect, owner-scoped, to Today, History, Ledger, Strategy, Settings or Overview. Deprecated financial POST routes reject the request with 405 rather than inadvertently replaying trades. Authenticator, Turnstile, health and staff admin remain protected.

## Approved prototype: remaining static differences

Playwright captures login, overview, Today, History, Ledger, Strategy, Breakdown, add-strategy, catalogue and settings at 1440px and 390px. It asserts width/height chrome geometry against docs/design/app_v2.html and no horizontal overflow or other-owner information. The prototype has synthetic performance figures and no authenticated login; no pixel parity with actual investments is claimed.

Known differences: v2 standard panel radius 8px versus prototype 14px; v2 accent #6ea8fe versus prototype desaturated cyan #9aa8bd. Login/TOTP/Turnstile and data-quality refusal states have no equivalent static prototype. Actual screenshots should be visually approved after CI artifacts are captured.

## Verified CI evidence and visual artifacts

The first draft-head run (commit `6450085`, Application checks run 39435300722)
**failed**: 385 Django tests run, seven expected skips, 74 failures and
three errors; the Chromium suite ran 29 tests with two failures and 13 errors.
Most regressions are tests still targeting removed legacy forms/routes.
Do not treat the old green-main test result as approval of this PR.

The new browser workflow first runs the Phase 8 visual capture suite with
`WEALTHARR_CAPTURE_V2_BASELINES=1`, then uploads the resulting real
1440px/390px PNGs as artifact `wealtharr-phase8-v2-screenshots` even
when the historic test suite remains red. This is evidence capture, not a
claim that the visual test or subsequent CI has passed.

## Important remaining dependency

The v2 Today read-only adapter still imports _price_status, _last_action and _next_end from tracker.cockpit. Removing that module would require extracting its logic and independently proving financial parity, which is explicitly outside layout scope. Its old endpoints are no longer routed. Some existing financial safeguard tests POST legacy cockpit handlers and expect legacy HTML. Do not delete or skip those tests before equivalent v2-endpoint cases are established. If these tests fail in CI, the draft PR is **not merge-ready**; a separate safe test-porting change is necessary.

Baseline (merged main): 395 Django tests, 7 expected skips, and 27 Chromium tests. No executable local Django checkout/runtime is available in this session, so local before/after counts are **not measured**. PR-head first-run test counts appear above; changes since that run have not been reverified. No production DB reset, migration execution or deployment was performed.
