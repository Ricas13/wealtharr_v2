# Section H — owner notifications and scheduler

Code review date: 2026-10-19. This is an **owner-only advisory message system**, not an automatic transaction system. Messages never access a broker, submit trades, transfer cash, validate ISA dealing permissions, or override 9Sig market-data holds.

## Choose destinations and categories

Open **Settings → Notifications**. Enable email and/or Discord as a destination, then choose whether each may receive:
- **Quarterly review digest:** one dated message for a current, source-supported quarter with outstanding proposed actions; proposed quantities are *approximate historical recommendations*, not live dealing prices
- **Urgent warnings:** 30 Down / provisional TQQQ 100%-up references, each a review-only event; a spike is never an instruction to place a 60/40 order
- **Data problems:** one message per problem identity and calendar week when holdings cannot be reliably valued
- **Review follow-ups:** at most one per week for an outstanding current-quarter review after the configured delay; after actual partial fills this is a bookkeeping reminder only
- **Contribution reminders:** a calendar-month target, based only on genuine recorded deposits; no direct debit, scheduled broker payment or fictitious deposit

The master destination switches and category matrix **both** have to permit a message; monthly reminders have one additional opt-in master toggle. Newly created owner preferences have email and Discord **off** by default. Email requires a destination email address plus working SMTP transport. Discord requires a secret webhook configured by a staff administrator. Only staff can view/change transport secrets or send test deliveries. Normal owners may view only their own delivery receipts and statuses. The scheduler health table is staff-only.

Push/mobile notifications are *not implemented*: neither a push provider nor a device token is configured.

## Delivery and immutable provenance

- `tracker/notification_routing.py` enforces fixed categories and per-owner channel choices; `tracker/services.py::dispatch` creates a unique `NotificationDelivery(owner, channel, event_key)` for each dated alert.
- Routed deliveries persist their **category** and the original subject/message. Repeated scheduler checks do not mutate already-saved dated advice or resend `SENT` notices. A message is **never a verified quote**.
- A failed send has a maximum of three attempts, with a persisted 5, 15 minute exponential delay. A stuck `SENDING` record may be retried only after 15 minutes.
- Every retry checks the latest destination switch and recorded alert category; disabling either permanently marks the pending receipt `CANCELLED`, rather than replaying it if the owner later opts in again. Older event keys use explicit prefix-based category inference; untyped historical receipts still obey destination masters.
- This is **at-least-once** network delivery. If a remote endpoint accepted a message but its acknowledgment disappeared, a retry can duplicate an advisory alert. No claim of global exactly-once transport delivery.
- `NotificationDelivery.status` tracks `SENDING`, `PENDING`, `FAILED`, `SENT`, and owner-muted `CANCELLED`. `attempts` and `next_retry_at` enable honest diagnostics; the status view never reveals stored message bodies or webhook credentials.

## Scheduler and troubleshooting

Use the **one canonical scheduler**, approximately hourly:

```sh
docker compose exec web python manage.py send_cockpit_notifications
```

The existing `compose.yml` `reminders` service runs `sync_market_and_signals` followed by `send_cockpit_notifications` and sleeps 3600 seconds; this is **configuration, not proof the owner's deployment is running**. Do not also schedule the legacy `send_due_notifications` alias: it delegates to this same pipeline and creates an avoidable duplicate invocation. To manually retry due messages without recalc, run:

```sh
docker compose exec web python manage.py retry_notifications
```

`tracker/notification_scheduler.py` records a `NotificationSchedulerRun` for each canonical scheduler iteration, including start/end, success/failure, new deliveries, attempted retries, successful retries and a **generic, redacted** failure code. An unfinished run older than an hour is flagged as interrupted; an otherwise last successful heartbeat older than three hours is flagged as overdue. These are operational observations, not evidence of quote freshness or recommendation correctness.

When a recipient reports a missing message, check category/channel settings, destination email, SMTP or Discord webhook configuration, recent owner delivery receipts, retry exhaustion and admin scheduler heartbeat. Test the configured channel manually; no test or scheduler should contain a broker account key. Never show SMTP passwords or webhook URLs in user-facing statuses.

## Boundaries and validation

- Additive schema migrations **0013** (category preferences and scheduler diagnostic runs) and **0014** (per-delivery category provenance) do not touch trading, strategy rules, holdings, fund balances or approved market observations.
- Automated tests cover owner isolation, category choices, archived delivery idempotency, immutable original payload, opt-out cancellation, retry budgets, scheduler diagnostics and failure-code confidentiality.
- SMTP/Discord **real outbound delivery** and production scheduler uptime remain unverified until the owner configures the destination and deployment. Running tests with stubbed transports is not an assertion that a real notification was received.
- Section I owns deployment and recovery. Section J owns browser-level visual/accessibility tests. Continue those sections only on the user's next instruction.
