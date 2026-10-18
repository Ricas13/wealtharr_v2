# Wealtharr v2

Private-first personal investment tracking with a responsive dark interface inspired by Infinidysk and Radarr.

## Included in this first foundation

- Public homepage, owner-only login, responsive user dashboard, account pages and user settings.
- Independent investment accounts (ISA, GIA, SIPP, other) and multiple instances of one strategy in separate tabs.
- A separate GBP cash and trade ledger for each tab: deposits, withdrawals, buys, sells, dividends and fees.
- Decimal-based value and invested-capital calculations, validation against unfunded trades and overselling.
- Manual GBP price marks and optional automated Yahoo GBP/GBp closing-price marks, plus an interactive chart based on recorded actual valuations.
- SMTP email and Discord notifications. Staff-only integration settings with encrypted credentials, monthly reminders, and optional hourly 9Sig market checks.
- Cloudflare Turnstile on production sign-in, secure Django sessions, CSRF protection and zero public registration.
- PostgreSQL via Docker Compose, test suite, CI and a /health/ endpoint.

**Methodology boundary:** Optional market monitoring now generates preliminary, **review-only 9Sig-style** quarterly target and TQQQ 30%-from-peak notices from Yahoo daily closing prices. This is **not** an independently verified replica of the attached workbook: skipped sell signals, eight-quarter streak, 100%-peak resets, cash-shortage handling and other exception branches remain unfinished. Neither trades nor dividends are invented. No broker orders are placed, automatic foreign-exchange conversion is supported, or UK ISA eligibility asserted. The master chart uses recorded actual valuations, never invented backtests. Yahoo's unofficial endpoint can fail or be delayed and is **not suitable for commercial SaaS without appropriate market-data rights**.

## Run locally

Requires Python 3.12 or newer.

1. Create and activate a virtual environment.
2. Install dependencies: pip install -r requirements.txt
3. Set environment variables DEBUG=1 and SECRET_KEY=your-local-development-key
4. Run python manage.py migrate
5. Create your owner account: python manage.py createsuperuser
6. Start: python manage.py runserver
7. Visit http://127.0.0.1:8000/

Follow the complete personal workflow: sign in → create an ISA or trading account → add a strategy tab → record starting cash and actual buys/sells → configure optional Yahoo monitoring using your exact GBP equity and safe-asset tickers → Check Yahoo prices → Overview and review pending actions. The same strategy can be tracked independently in two accounts.

For tests, run python manage.py test tracker and python manage.py makemigrations --check --dry-run.

## Production with Docker and Traefik

1. Create a **private** .env.production with DEBUG=0, a long stable SECRET_KEY, ALLOWED_HOSTS=wealtharr.vpn4u.cc, CSRF_TRUSTED_ORIGINS=https://wealtharr.vpn4u.cc, TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY from Cloudflare. When DEBUG=0, Turnstile is required for login and fails closed without valid keys.
2. Create a separate private .env holding POSTGRES_PASSWORD. The initial Compose configuration interpolates this into DATABASE_URL; avoid URL-reserved characters in this password, or update to an appropriately encoded URL/secrets mechanism before deployment.
3. Run docker compose up -d --build. The app binds to **127.0.0.1:8088** by default. Route your existing Traefik HTTPS proxy to that port and **do not expose Gunicorn directly on the internet**. Check https://wealtharr.vpn4u.cc/health/ after configuring DNS/proxy.
4. Create the owner with docker compose exec web python manage.py createsuperuser.
5. Inside Wealtharr, My settings configures notification destinations/preferences. Admin control configures SMTP/Discord webhook; the advanced admin can enable/hide strategy templates.
6. Validate docker compose exec web python manage.py check --deploy and docker compose exec web python manage.py test tracker.

The reminders container checks missing monthly contribution targets **and configured 9Sig market reviews** hourly. It does not execute trades. Do not run multiple scheduler replicas. SMTP/Discord credentials are encrypted using a key derived from SECRET_KEY — keep this key on restoration. Make regular PostgreSQL backups using pg_dump and test restoring them; the Compose volume alone is not a backup.

## Accounting and security decisions

- Each strategy instance owns its own cash ledger and transactions, even when two tabs refer to the same account. Record a transfer between tabs as a withdrawal and deposit, never duplicate an external deposit.
- Position quantity = buys - sells. Current value = cash + all held quantities × available GBP marks (manual or Yahoo). **If any held price is missing, total wealth and gain are hidden** rather than understated.
- Investment result = wealth - (deposits - withdrawals), not a time-weighted return percentage. The wealth chart contains saved value and net contributions as separate series.
- Entries are append-only through the user interface and must be recorded chronologically. No shorting, debt, negative cash, automatic tax lots, dividend withholding, FX, splits or broker reconciliation in this initial version.
- Price marks are labeled by source: manual or Yahoo. Missing quote history, unsupported ticker currencies and stale market data prevent new strategy advisories rather than triggering guessed trades. Old manual marks can still misstate displayed holdings.
- The owner interface is designed for private use, not public SaaS or regulated investment advice. Trading decisions remain with you.

Remaining work before exact strategy reliance or SaaS launch: full independently tested Excel strategy-rule parity, licensed robust price and FX services, time-weighted return analytics, verified UK instrument/Trading 212 ISA eligibility, read-only broker reconciliation, and production operational hardening. Stripe is intentionally not configured.

## Enable 9Sig market monitoring

On the 9Sig strategy tab, expand **Configure Yahoo monitoring**, enter two distinct exact Yahoo GBP tickers (3× Nasdaq equity and the safe-asset line), tick **Enable automatic Yahoo closes and advisory 9Sig reviews**, and save. Use **Check Yahoo prices** to fetch available history and immediately review eligible notices. When configured, the Compose reminders service runs `python manage.py sync_market_and_signals` hourly; it also records an actual GBP valuation snapshot only when every holding across the owner's strategies has a sufficiently recent price mark.

- The workbook grows the stock-fund signal line **9% every quarter**, not 9% annually. The 30-Down trigger uses **SPY** (a rolling eight-quarter high); **TQQQ** provides the 100%-up reference. A 30-Down event **skips sell signals**, rather than ordering an immediate half-position sale. The app uses verified quarter-end closing observations, not an arbitrary mid-quarter quote.
- TQQQ is the **US model reference for the spike rule**. A UK-listed daily-reset leveraged product is not automatically equivalent. Verify the issuer, trading line, instrument currency, leverage/reset mechanism, Trading 212 availability and ISA eligibility yourself.
- Yahoo `GBp`/GBX quotes are converted from pence to pounds; no USD→GBP conversion takes place. Outdated/mismatched data does not produce a new signal.
- Instructions are review-only approximations with duplicate suppression and a record of acknowledgement; no trade is booked until **you** enter its actual fill.
- Other strategy templates remain manual tracking only. Only the Nasdaq 3× strategy has optional signal monitoring. The user-facing admin controls remain separate from Django administration.


### Evidence for UK instruments (not a broker-equivalence promise)

- **LQQ3.L**, LSE GBp line of WisdomTree Nasdaq 100 3x Daily Leveraged, ISIN **IE00BLRPRL42**. Issuer: [WisdomTree product and trading-line list](https://www.wisdomtree.com/gb/products/equities/wisdomtree-nasdaq-100-3x-daily-leveraged). WisdomTree lists the ETP as **ISA eligible** and labels it daily-reset, fully-collateralised swap exposure. It is **not** mechanically identical to US TQQQ through quarterly periods; compare fees, currency, counterparty exposure and reset mechanisms.
- **ERNS.L**, LSE GBP line of iShares GBP Ultrashort Bond UCITS ETF, ISIN **IE00BCRY6441**. Issuer: [iShares ERNS product/listing](https://www.ishares.com/uk/individual/en/products/258120/ishares-ultrashort-bond-ucits-etf). iShares lists ISA eligibility. It is a GBP corporate/quasi-government ultrashort bond fund, **not** the same exposures as AGG.
- Neither issuer page confirms availability in **your particular Trading 212 ISA or GIA account**. Confirm in the broker's instrument screen before entering ticker quantities or acting on a proposed change.
- Ticketers above are optional research candidates, **not hardcoded purchases**. User chooses their actual held instrument and Yahoo mapping. Dividends/distributions, spreads, trade fees and FX cash flows must be entered as actual ledger events; no equivalent US return is invented.

### Automation guardrails

- Every review uses the last *completed* quarter's closing date and actual recorded ledger positions no later than that date. It refuses incomplete currency or SPY/TQQQ history, unrelated holdings, negative cash and unknown purchases rather than fabricating an answer.
- Daily price marks and historical settlement marks are kept separate. The graph's automatic snapshot is recorded at the quote date only when **all** positive holdings have fresh, source-verified prices, matching observation dates and no later transactions. Manual snapshot capture remains available.
- Remaining spreadsheet-exception branches rely on owner-entered YES/NO flags and have not all been independently transcribed into verifiable stateful logic. Do not treat the suggestions as a certified Excel clone. Use the source workbook for trade approval.


## Automated PostgreSQL backup and guarded recovery

The Compose 'backups' service runs pg_dump with PostgreSQL custom-format archives when the database becomes healthy and every 24 hours thereafter. Archives live on the host under ./backups/ with private directory/file permissions and 30-day retention. The Postgres data volume alone is **not** a backup. Keep encrypted **off-host** copies of the archives and the separate SECRET_KEY securely.

Test restore on a disposable installation first. From your deployment directory:

    WEALTHARR_CONFIRM_RESTORE=YES_REPLACE_WEALTHARR bash scripts/restore_backup.sh ./backups/wealtharr-YYYYMMDDTHHMMSSZ.dump

**WARNING:** Restoration replaces the current database. Any entries created after the archived point are lost. The helper stops the app/scheduler/backup service, restores with pg_restore and restarts containers. The scripted commands are not a substitute for a live restore drill on your Oracle machine.

Failed email/Discord deliveries persist the redacted status and retry up to three times, with 5- and 15-minute delays. Admin can requeue a failed delivery after fixing credentials. These alerts are at-least-once; a lost network acknowledgement can rarely cause duplicate alerts, but never automatic trading.
