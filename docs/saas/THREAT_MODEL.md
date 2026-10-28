# Threat model (short form)

Assets: users' financial records (ledger, holdings, reviews), alert destinations and connection secrets, account credentials, billing state, the audit log.

| Threat | Controls | Residual / owner |
|---|---|---|
| One user reads or changes another's data | Tenant managers fail closed; every owner lookup goes through `tenant_scope`; jobs run per owner; optional PostgreSQL row-level security (`docs/saas/RLS.md`); `tenancy_check` | RLS is opt-in; admin superusers see everything by design |
| Account takeover | Password validators (12+), sign-in throttling per user/address, optional authenticator, password reset by emailed one-time link, session list and revoke, audit of sign-ins | No mandatory 2FA; email account security is the user's |
| Abuse of an account or stolen session | Per-user write ceiling (`WRITE_RATE_LIMIT_PER_MINUTE`), CSRF, `HttpOnly`/`Secure`/`SameSite` cookies, strict response headers | Limits are per user, not per address |
| Stored credentials leaked (SMTP, Discord, Telegram, authenticator seeds) | Fernet encryption with a key separate from `SECRET_KEY`; `DATA_ENCRYPTION_KEYS_PREVIOUS` + `migrate_data_encryption` rotate it; secrets never in logs or exports | Key custody is the operator's; rotate on staff change |
| Operator or support misuse | Support access is user-granted, time-boxed, metadata-only and audited; admin actions append to a hash-chained audit log (`verify_audit_chain`) | A superuser with database access can still read everything |
| Forged payment events | Stripe webhook off unless a secret is set; HMAC-verified with 5-minute tolerance; event ids stored once | PayPal is manual |
| Account enumeration | Sign-up and reset give the same answer whether or not an address exists | Usernames are unique, so sign-up can still reveal a taken username |
| Supply chain | Pinned requirements; `pip-audit`, `bandit`, secret scanning and CodeQL in CI | Transitive vulnerabilities appear between scans (weekly job) |
| SSRF through user-supplied URLs | Only the Discord webhook is user-supplied and is matched against a strict Discord pattern | |
| Data loss | Operator backups (see runbook); account closing keeps append-only records | Restore drills are the operator's |

Out of scope here: legal and regulatory obligations (the operator's), brokers (none connected), denial of service at the network layer (put a proxy or CDN in front).
