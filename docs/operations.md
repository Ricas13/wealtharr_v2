# Operations notes


**Private owner install / update / server migration:** [Portable deployment](PORTABLE_DEPLOYMENT.md) — `bash wealtharr.sh setup`, `start`, `update`, `export` and `restore-fresh`. This supersedes the older example commands below.

Private-first personal investment tracking with a responsive dark interface inspired by Infinidysk and Radarr.

## Current private-owner cockpit (PR #11)

The active redesign is documented in [RESUME.md](RESUME.md) and
[Section J private ship checklist](private-ship-checklist.md).
It supports **one UK owner tracking a fixed, version-pinned 9Sig strategy in
a Trading 212 Stocks & Shares ISA**, with actual dated contributions, manual
owner-confirmed fills, fail-closed GBP market observations and read-only audit
history. It never connects to Trading 212 (including read-only APIs),
executes broker trades, invents backtests, or offers public signup/billing.
The general-purpose account and preset flows below are retained as
historical foundation documentation, **not** the supported onboarding path.

Local browser test commands and outstanding production/financial acceptance
gates are in the Section J ship checklist. Production deployment and
independent workbook parity are **not automatically verified by CI**.

## Included in this first foundation

- Public homepage, owner-only login, responsive user dashboard, account pages and user settings.
- Independent investment accounts (ISA, GIA, SIPP, other) and multiple instances of one strategy in separate tabs.
- A separate GBP cash and trade ledger for each tab: deposits, withdrawals, buys, sells, dividends and fees.
- Decimal-based value and invested-capital calculations, validation against unfunded trades and overselling.
- Manual GBP price marks and optional automated Yahoo GBP/GBp closing-price marks, plus an interactive chart based on recorded actual valuations.
- SMTP email and Discord notifications. Staff-only integration settings with encrypted credentials, monthly reminders, and optional hourly 9Sig market checks.
- Cloudflare Turnstile on production sign-in, secure Django sessions, CSRF protection and zero public registration.
- PostgreSQL via Docker Compose, test suite, CI and a /health/ endpoint.

**Methodology boundary:** Optional market monitoring produces review-only 9Sig quarterly targets and rule notices from verified Yahoo daily closing prices. The core line, 30 Down sell skips, eight-quarter expiry, 100%-up spike warning and workbook 81%-of-total-reserves large-buy throttle are tested, but not every discretionary override in the uploaded workbook has been independently reproduced. Neither trades nor dividends are invented. No broker orders are placed, automatic foreign-exchange conversion is supported, or UK ISA eligibility asserted. The master chart uses recorded actual valuations, never invented backtests. Yahoo's unofficial endpoint can fail or be delayed and is **not suitable for commercial SaaS without appropriate market-data rights**.

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

1. Create a **private** .env.production with DEBUG=0, a long stable SECRET_KEY, ALLOWED_HOSTS=app.example.com, CSRF_TRUSTED_ORIGINS=https://app.example.com, TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY from Cloudflare. When DEBUG=0, Turnstile is required for login and fails closed without valid keys.
2. Create a separate private .env holding POSTGRES_PASSWORD. The initial Compose configuration interpolates this into DATABASE_URL; avoid URL-reserved characters in this password, or update to an appropriately encoded URL/secrets mechanism before deployment.
3. Run docker compose up -d --build. The app binds to **127.0.0.1:8088** by default. Route your existing Traefik HTTPS proxy to that port and **do not expose Gunicorn directly on the internet**. Check https://app.example.com/health/ after configuring DNS/proxy.
4. Create the owner with docker compose exec web python manage.py createsuperuser.
5. Inside Wealtharr, My settings configures notification destinations/preferences. Admin control configures SMTP and the Discord bot (people connect their Discord for DMs and/or add their own channel webhook); the advanced admin can enable/hide strategy templates.
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

On the 9Sig strategy tab, expand **Configure Yahoo monitoring**, check the account-selected issuer-mapped GBP tickers (3× Nasdaq equity and the safe-asset line), tick **Enable automatic Yahoo closes and advisory 9Sig reviews**, and save. Use **Check Yahoo prices** to fetch available history and immediately review eligible notices. When configured, the Compose reminders service runs `python manage.py sync_market_and_signals` hourly; it also records an actual GBP valuation snapshot only when every holding across the owner's strategies has a sufficiently recent price mark.

- The workbook grows the stock-fund signal line **9% every quarter**, not 9% annually. The 30-Down trigger uses **SPY** (a rolling eight-quarter high); **TQQQ** provides the 100%-up reference. A 30-Down event **skips sell signals**, rather than ordering an immediate half-position sale. Ordinary rebalance signals use verified completed-quarter closes. A potential 100%-up TQQQ spike can generate a separate urgent notice after a verified DAILY USD close during a quarter; the exact preceding rebalance execution-price trigger must be checked before acting.
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


### Manual 60/40 reset and subsequent 9Sig cycles

When a completed-quarter rule or urgent mid-quarter spike warning proposes reviewing a 60/40 reset, **Mark reviewed** does not execute or confirm it. Trade manually at the broker, record the real executed fills with fees, and refresh both GBP Yahoo marks. Then choose **Confirm executed reset and start next 9Sig cycle** from that acknowledged notice. Wealtharr validates that trades happened after the notice, the latest prices are recent, no unmodelled positions are present and recorded equity is within 5 percentage points of 60% of the actual account value. It writes an immutable baseline with the confirmed shares/cash, preventing future targets from incorrectly compounding the *pre-reset* initial balance. Without a confirmed new baseline, later quarter signals pause rather than guessing that the recommendation was executed.


## Live owner paper-tracking mode (no Trading 212 connection)

On Overview or Create Strategy, choose **Start isolated 9Sig paper trial**. This creates a separate PAPER account with **£1,000 virtual capital**, buying £600 of LQQ3.L and £400 of ERNS.L at a common verified GBP Yahoo daily close. Fractional shares are fictional: NO real purchases, transfers or trades are made. The setting is opt-in; it never changes any existing ISA or GIA holdings. Clicking it twice returns to the existing paper trial instead of double funding.

The monthly contribution target is £500, **planned only**. Neither virtual nor real monthly contributions happen automatically. Record manual paper-only deposits to model additional contributions. Monthly contribution reminders for this paper tab start disabled.

The trial gets its own growth chart. The regular Overview real-money total and consolidated net deposits EXCLUDE virtual paper assets. The scheduled hourly checker refreshes prices and records a strategy valuation only when all quoted GBP closes are sufficiently recent and no transaction is newer than the close. No fictitious backtest, dividends or corporate actions are generated.

Quarterly 9Sig signals begin after the first completed quarter after the paper entry; a trial beginning October 2026 will not generate a new Q4 target alert until market data for the quarter ending 31 December 2026 becomes available. Notifications remain review-only and may differ from every exceptional branch in the spreadsheet. False positives can be acknowledged in-app without placing orders or changing actual holdings.

If the Yahoo feed is down, mismatched in currency or provides different closing dates for the two UK lines, startup fails safely without creating a half-funded account.

## Manual Oracle deployment after merging a PR

You deploy manually on Oracle after deciding a PR is ready. CI never deploys or configures the server.

From your existing Oracle deployment checkout, after taking a database backup:

    git fetch origin
    git checkout main
    git pull --ff-only origin main
    docker compose up -d --build
    docker compose exec web python manage.py check --deploy
    docker compose ps
    curl -fsS https://app.example.com/health/

Maintain your private .env and .env.production on the Oracle server. Production login requires valid Cloudflare Turnstile keys; persistent PostgreSQL storage, SMTP/Discord integrations and the hourly reminders container must already be configured. Verify routing through Traefik to the server's bound port 8088 and check scheduler logs with:

    docker compose logs --tail=100 reminders

Do not restore, overwrite, or migrate a different project without confirming the deployment directory and backup first. Wealtharr does not deploy itself or connect to Trading 212.


## Start tracking a real Trading 212 9Sig portfolio

On the Overview dashboard, choose **Track my real 9Sig** (NOT the optional paper trial). Enter your actual UK Stocks & Shares ISA or GIA name, starting cash deposit and deposit date, GBP-denominated Yahoo equity and safe-asset trading-line symbols, and £500 monthly target.

On your new strategy tab, use **Record activity** to enter actual Trading 212 BUY/SELL fills in chronological order: execution date, symbol, quantity, unit GBP price and fees. The first setup step **does not fabricate purchases** or broker balances. If you already owned positions before installing Wealtharr, reconstruct the historical actual deposit and fills before expecting the historical quarterly signal line to match your record. Confirm the LSE ticker spelling and instrument availability in your own broker account; the sample LQQ3.L and ERNS.L listings are research examples, not a claim they replicate TQQQ/AGG.

Use **Check Yahoo prices** to confirm GBP closes and reference data. The scheduler checks markets hourly but sends a rebalance alert only on a completed-quarter BUY/SELL/reset action, or a newly detected 30 Down. No-action routine reviews are preserved in history without pushing notifications. Dates and suggested unit quantities are based on closing prices; your manually recorded fills determine actual cash and future holdings. Email or Discord must be configured in My settings and Admin control, with a working test delivery.

**Workbook correction:** Contributions after the initial equity purchase raise the quarterly line by **half their amount at that quarter's close**, without 9% growth until later quarters. The eight-quarter 60/40 safety reset relates to a running 30 Down window, **not** any eight quarters without an equity sale. The initial equity basis includes only the initial purchase-day equity buys; later buys are not automatically mistaken for initial capital. Exact workbook discretion flags and potential instrument differences still require the owner's judgment.


## Automatic instrument selection for a real 9Sig account

The ordinary real-account setup now asks for **strategy, country, broker, account wrapper, starting cash and monthly contribution**, rather than asking an inexperienced owner to supply tickers. The instrument table on the screen is resolved by the server from an explicit, evidence-linked mapping keyed by all four selection dimensions. The normal strategy tab repeats the mapping and uses a BUY/SELL instrument dropdown instead of a free-text ticker entry.

**Supported now:** Nasdaq-100 3x quarterly target (9Sig-style), United Kingdom, Trading 212, Stocks & Shares ISA or Invest/GIA, GBP accounting. All other combinations fail closed with a clear message rather than silently substituting products. This intentionally does not pretend the other strategy templates have broker-specific mappings.

- **60% high-risk equity**: WisdomTree Nasdaq 100 3x Daily Leveraged, **LQQ3**, LSE GBX quoted line, **ISIN IE00BLRPRL42**, Yahoo **LQQ3.L** (raw pence price normalized to GBP). Issuer source: https://www.wisdomtree.com/gb/products/equities/wisdomtree-nasdaq-100-3x-daily-leveraged . The issuer explicitly lists the product as ISA-eligible; it is a leveraged ETP and resets its leveraged exposure daily.
- **40% defensive sleeve**: iShares GBP Ultrashort Bond UCITS ETF (Dist), **ERNS**, LSE GBP trading line, **ISIN IE00BCRY6441**, Yahoo **ERNS.L**. Issuer source: https://www.ishares.com/uk/individual/en/products/258120/ishares-ultrashort-bond-ucits-etf . The issuer lists this fund as ISA-eligible. This is a GBP ultrashort corporate bond ETF, not an equivalent of the US AGG aggregate bond benchmark.
- **Trading 212 listings**: https://www.trading212.com/trading-instruments/invest/LQQ3.GB and https://www.trading212.com/trading-instruments/invest/ERNS.GB show the products in the **Invest catalogue**. This is **not proof** that the products are currently offered or permitted in every user's ISA. Trading 212 says complex leveraged ETPs may require an appropriateness assessment: https://helpcentre.trading212.com/hc/en-us/articles/10397198010525-Appropriateness-assessment-test-in-Invest-ISA-SIPP . Confirm both ISINs in the **exact chosen ISA/GIA account** before starting; this is an explicit required checkbox in setup.
- These are **model-compatible research candidates**, not a mathematically identical US 9Sig fund pair or a guarantee of personal suitability. Daily leverage compounding, FX exposure, bond duration, fees and spread cause genuine differences. The model still uses US **SPY** / **TQQQ** as rule-trigger references, not as UK trading instructions.
- The software stores account country and broker independently. Existing accounts default to **unknown broker** and are never automatically asserted to be Trading 212; a newly confirmed real setup can label an existing owner account correctly.
- Submitting alternate ticker field values cannot override a mapped strategy. Once mapped, the regular trade form offers LQQ3.L and ERNS.L as the canonical accounting symbols (displaying friendly names) and rejects unrelated buys/sells. This avoids confusing Trading 212's LQQ3 label with Yahoo's LQQ3.L price symbol.
- Neither recommendation nor creating a portfolio **executes a trade** or manufactures holdings. Actual deposits, fills, commissions and existing real positions must still be entered from Trading 212 records. Rebalances remain manual.

**9Sig methodology research, correction rationale and remaining best-guess boundaries:** see [docs/9sig-methodology.md](9sig-methodology.md). Never claim a GBP daily-reset ETP exactly replicates the US TQQQ fund's historical results.


## Owner-only 9Sig cockpit (feature branch)

Authenticated users land at **/cockpit/** with Today, History, Ledger and Settings.
The guided rebalance only saves actual owner-confirmed fills; CSV imports preview before writing; no broker trading API is used. The legacy /app/ screens remain accessible.
Read **docs/RESUME.md** to continue this branch in another ChatGPT chat, **docs/INSTALL.md** for database/scheduler wiring, and **docs/FINANCIAL_QA.md** before treating any quarterly signal as verified. The strategy implementation has documented Excel-parity gaps and cannot be assumed certified.
