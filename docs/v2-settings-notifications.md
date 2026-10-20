# Wealtharr v2 — Phase 7 owner Settings and notifications

This phase builds on the Phase 6 branch. It does not alter the versioned 9Sig engine,
instrument identities, ledger replay, confirmed fills, broker access or automated trading.

## Notification routing

- **Account-wide owner switches** and five category-by-channel preferences continue
  to use the existing `NotificationPreference`. These switches control email and
  Discord for the whole account. The owner now chooses a follow-up delay from **1–30
  calendar days** (default **7**), with at most one weekly review follow-up after it.
- **Per-strategy route choices**, recorded during v2 onboarding, are now actually
  applied to quarterly, urgent, data, outstanding-review and contribution events.
  Internal `followup` corresponds to the enrolment key `unacted`. Only an enabled
  owner-wide category/channel **and** a true strategy route may send. Invalid or
  missing new-v2 route maps fail closed. Existing unconfigured legacy portfolios
  inherit the existing owner-wide routing until edited.
- **Immutable delivery scope:** new alert receipts store nullable originating
  `strategy_id` (migration 0019), so retries recheck the same owner's current
  master/category/strategy switches. A muted pending receipt becomes
  `CANCELLED` and cannot later be resurrected by re-enabling alerts.
  Older receipts lack a strategy FK and retain previous legacy retry behaviour.
- Advice delivery is **at least once** at transport level; receiving an SMTP/HTTP
  acknowledgment does not confirm inbox or device receipt. No notification
  executes a trade or verifies a proposed quote.

## Settings, credentials and health

- Django server-rendered `/settings/` offers master destinations, category
  choices, the review delay, per-instance routes, private recent receipt status,
  password and owner TOTP setup links. All POST mutations require authenticated
  owner access and Django CSRF. Sending a live email/Discord test is a
  **staff-only POST**. Existing staff-only IntegrationForm remains the only
  method for configuring encrypted SMTP and webhook credentials.
- The normal owner cannot view other owners' portfolios, messages, receipts,
  credentials, or staff diagnostics. Staff see only a **bounded, redacted**
  scheduler heartbeat from the already persisted notification run log.
- No backup timestamp or successful restore is inferred from application
  existence. An encrypted off-host archive and isolated real restoration remain
  owner-operated acceptance checks.
- The approved prototype includes optional ntfy, account-wide export,
  logout-other-sessions, and destructive reset. They are **not** silently wired
  to fake functionality: push has no transport, a complete one-click bundle
  would need verified private export coverage, and risky identity/data
  operations require separate security acceptance. Existing per-strategy
  audited Ledger export remains available.
- No UK ISA permission/quote-unit endorsement or real-money advice readiness
  follows from configuring an alert.

## Validation

`tracker/test_v2_phase7.py` covers master preferences, 1–30 day validation,
per-strategy category suppression, followup/unacted mapping, legacy
compatibility, retry cancellation and immutability, cross-owner privacy,
staff-only test sending and redaction. `browser_tests/test_v2_phase7.py`
exercises live 390px and 1440px Settings forms and private strategy routing.
Confirm **the latest stacked PR head's** Django/migration and Chromium jobs
actually pass before merging. Synthetic tests do not prove transport delivery.

**Merge order:** Phase 1 #12 → Phase 2 #13 → Phase 3 #15 → Phase 4 #17 →
Phase 5 #19 → Phase 6 #22 → Phase 7. Each PR should be retargeted to
`main` only after its predecessor has merged and CI remains green. Separately
merged 9Sig methodology and deployment changes already on `main` must
be reconciled and retested before the stack's production release.
