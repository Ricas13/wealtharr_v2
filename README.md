# Wealtharr v2

Private-first personal investment tracking with a responsive dark interface inspired by Infinidysk and Radarr.

## Included in this first foundation

- Public homepage, owner-only login, responsive user dashboard, account pages and user settings.
- Independent investment accounts (ISA, GIA, SIPP, other) and multiple instances of one strategy in separate tabs.
- A separate GBP cash and trade ledger for each tab: deposits, withdrawals, buys, sells, dividends and fees.
- Decimal-based value and invested-capital calculations, validation against unfunded trades and overselling.
- User-entered GBP price marks for held instruments, and an interactive master chart based on saved actual valuations.
- SMTP email and Discord notifications. Staff-only settings page with encrypted credentials and test deliveries; planned monthly contribution reminders via a scheduler.
- Cloudflare Turnstile on production sign-in, secure Django sessions, CSRF protection and zero public registration.
- PostgreSQL via Docker Compose, test suite, CI and a /health/ endpoint.

**Boundary:** The seeded 9Sig-style strategy is a *tracking template*, not a 9Sig calculation engine. This release does not generate rebalance signals, quarterly targets, TQQQ 30-down alerts or broker orders. It does not download live market prices or convert currencies. Prices are manually entered in GBP. The master chart displays recorded valuations, never backfilled/invented historical performance.

## Run locally

Requires Python 3.12 or newer.

1. Create and activate a virtual environment.
2. Install dependencies: pip install -r requirements.txt
3. Set environment variables DEBUG=1 and SECRET_KEY=your-local-development-key
4. Run python manage.py migrate
5. Create your owner account: python manage.py createsuperuser
6. Start: python manage.py runserver
7. Visit http://127.0.0.1:8000/

Follow the complete personal workflow: sign in → create an ISA or trading account → add a strategy tab → record starting cash and actual buys/sells → mark holding prices in GBP → Overview → Save today's valuation. The same strategy can be tracked independently in two accounts.

For tests, run python manage.py test tracker and python manage.py makemigrations --check --dry-run.

## Production with Docker and Traefik

1. Create a **private** .env.production with DEBUG=0, a long stable SECRET_KEY, ALLOWED_HOSTS=wealtharr.vpn4u.cc, CSRF_TRUSTED_ORIGINS=https://wealtharr.vpn4u.cc, TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY from Cloudflare. When DEBUG=0, Turnstile is required for login and fails closed without valid keys.
2. Create a separate private .env holding POSTGRES_PASSWORD. The initial Compose configuration interpolates this into DATABASE_URL; avoid URL-reserved characters in this password, or update to an appropriately encoded URL/secrets mechanism before deployment.
3. Run docker compose up -d --build. The app binds to **127.0.0.1:8088** by default. Route your existing Traefik HTTPS proxy to that port and **do not expose Gunicorn directly on the internet**. Check https://wealtharr.vpn4u.cc/health/ after configuring DNS/proxy.
4. Create the owner with docker compose exec web python manage.py createsuperuser.
5. Inside Wealtharr, My settings configures notification destinations/preferences. Admin control configures SMTP/Discord webhook; the advanced admin can enable/hide strategy templates.
6. Validate docker compose exec web python manage.py check --deploy and docker compose exec web python manage.py test tracker.

The reminders container checks missing monthly contribution targets hourly. It does not execute trades. Do not run multiple scheduler replicas. SMTP/Discord credentials are encrypted using a key derived from SECRET_KEY — keep this key on restoration. Make regular PostgreSQL backups using pg_dump and test restoring them; the Compose volume alone is not a backup.

## Accounting and security decisions

- Each strategy instance owns its own cash ledger and transactions, even when two tabs refer to the same account. Record a transfer between tabs as a withdrawal and deposit, never duplicate an external deposit.
- Position quantity = buys - sells. Current value = cash + all held quantities × latest manual GBP marks. **If any held price is missing, total wealth and gain are hidden** rather than understated.
- Investment result = wealth - (deposits - withdrawals), not a time-weighted return percentage. The wealth chart contains saved value and net contributions as separate series.
- Entries are append-only through the user interface and must be recorded chronologically. No shorting, debt, negative cash, automatic tax lots, dividend withholding, FX, splits or broker reconciliation in this initial version.
- A price mark is user-provided, not a verified quote. Update it when needed; stale prices can misstate value.
- The owner interface is designed for private use, not public SaaS or regulated investment advice. Trading decisions remain with you.

Next steps after validating the full basic tracking flow: independently tested 9Sig calculation/signal rules, authoritative historical price and FX services, time-weighted return analytics, verified UK instrument eligibility and read-only Trading 212 imports. None is claimed delivered by this initial PR.
