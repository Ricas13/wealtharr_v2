# Support access and the platform-admin split

- **Platform admin = superuser.** Plans, billing events, the audit log and the support area are superuser-only; ordinary staff see none of them.
- **A user can allow support** (Settings → Security → "Let the site owner look…", `/support-access/`) for 1 h, 4 h, 24 h or 3 days, and switch it off at any time. Nothing is on by default.
- **While allowed**, a superuser sees `/support/<id>/`: plan, joined/last sign-in, counts of accounts, ledger entries and reviews, strategy kinds, which alert channels are on, 30-day delivery counts and recent account events. **No amounts, prices, tickers, ledger rows or messages**, and nothing can be changed from there. With no consent or after expiry the page is a 404.
- **Every look** is an audit event (`support.viewed`) in the user's own chain, listed on their support-access page with who looked and when. Grant and revoke are audited too.
- This does not replace the Django admin, which a superuser can still use. Treat the admin as full access; the consent model governs the support pages only.
