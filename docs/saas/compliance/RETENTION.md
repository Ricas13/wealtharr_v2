# Retention (what the software does today)

| Data | Kept | Removed by |
|---|---|---|
| Account (username, email, name) | While the account is open | Closing the account blanks email and names and renames the user `deleted-<id>` |
| Alert destinations, webhooks, authenticator, saved connections | While set | Closing the account; or the person removing them |
| Ledger, reviews, fills, baselines, snapshots | Append-only; kept after closing, without the person's name | Not deleted by the software. Operator decides a period and purges (below) |
| Audit log | Append-only, hash-chained | Not deleted; holds ids and actions, not amounts |
| Sign-in throttling counters | Minutes to hours | Expire by themselves |
| Invite-request emails | Until the owner removes them | Admin |
| Backups | Operator's schedule | Operator's rotation |

**Purging closed accounts.** The financial tables are append-only on purpose, so there is no one-click hard delete. To erase a closed account's records at the end of the retention period, take `user_bundle export` (if you must keep a sealed copy), then delete the user's rows in a controlled maintenance window with the application stopped, and re-run `tenancy_check`. Decide and write down the period; a common starting point is a fixed number of years after closure, but that is a legal question for the operator.

**Erasure requests.** The same procedure, on request, with the audit log entry left in place.
