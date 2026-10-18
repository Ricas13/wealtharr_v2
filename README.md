# Wealtharr v2

A private-first, Radarr-inspired investment tracker with InfiniDysk-style dashboard cards. Multiple accounts per user, 9Sig tracking, a transaction ledger, Yahoo market data, quarterly review notices, email/Discord, and admin controls.

## Status and investment safety

**Personal MVP.** The advisory engine uses a conservative *9Sig-style* interpretation of the attached Kelly Letter spreadsheet: 9% annual equity target growth, 40% safe baseline, 10% protected reserve, and a TQQQ 30%-from-peak review. The original spreadsheet has additional rules (skipped sell signals, eight-quarter rule, resets and shortages) **not yet replicated or independently validated**. Do not treat suggestions as exact workbook instructions or broker orders. Trades are executed manually.

Yahoo Finance chart data is an unofficial, rate-limited source with no service guarantee or commercial data licence granted to this project. Do not commercialise the feed without appropriate rights. Currency-mismatched or stale quotations are rejected, not guessed. TQQQ is the US model reference, **not an assertion that any UK-listed instrument is equivalent or ISA eligible**.

## Start with Docker

1. Copy '.env.example' to '.env'. Set 'POSTGRES_PASSWORD' to a secure database password, 'BOOTSTRAP_ADMIN_EMAIL', 'BOOTSTRAP_ADMIN_PASSWORD' (at least 12 characters), and 'CRON_TOKEN' (24+ characters of random text).
2. Run 'docker compose up -d --build'. The app migrates the database and creates the admin account exactly once. Changing bootstrap secrets later does not overwrite an existing user.
3. Open 'http://127.0.0.1:3000', sign in, create a 9Sig account and enter existing positions or cash.
4. Record actual trades and use 'Refresh prices' to request daily market values. An hourly scheduler runs automatically.
5. Optionally configure 'SMTP_HOST', 'SMTP_PORT', 'SMTP_USER', 'SMTP_PASSWORD', 'SMTP_FROM' and 'DISCORD_WEBHOOK_URL'. Change recipient preferences in Account → Notifications.

For local development, run Postgres, 'npm install', 'npm run db:migrate', 'npm run db:bootstrap', 'npm run dev'. Trigger a job with: 'curl -X POST http://localhost:3000/api/cron -H "Authorization: Bearer YOUR_CRON_TOKEN"'. Run 'npm test' and 'npm run build'.

## What works

- Public homepage; invite-only username/password access; admin-created additional accounts and per-user portfolios.
- Multiple separately named 9Sig accounts: ISA, general investments and other account types.
- Deposit, buy, sell, withdrawal, dividend and fee events. Opening shares are separate from cash; buys and sells update the account ledger, cost basis and cash.
- Per-account portfolio graphs, aggregated same-currency growth graphs, performance metrics, holdings and unacknowledged actions.
- Yahoo ticker mapping and GBp-to-GBP normalization, with missing and mismatched prices blocked. One trading currency per portfolio.
- Scheduled price checks, quarter reviews, 30%-from-peak alerts, de-duplicated recommendations and limited-retry notifications.
- Admin strategy enable/disable, invite-only users, email/Discord readiness, and a deliberately unconfigured Stripe placeholder.

## Methodology boundary

The strategy tracker does **not** implement all of the workbook's extraordinary conditions. Current advisory signal line = initial equity cost times '1.09^(quarters_elapsed/4)'. Excess equity triggers a proposed sale; a shortfall prompts an equity buy limited by cash and up to 90% of safe value. TQQQ's closes since the portfolio's tracking start trigger a half-position sale review once a peak drawdown reaches 30%. No trades are automatically executed. Where exceptional rule fidelity matters, verify against the supplied spreadsheet and the strategy author's instructions first.

Deposits affect account value; gain/loss is calculated net of deposits and opening holdings. Historic snapshots accumulate from first use rather than pretending to backtest the past. Mixed-currency accounts deliberately lack a fabricated total until proper FX support exists.

## Security and future SaaS

Password hashes use bcrypt; logins use revocable opaque hashed session tokens in HttpOnly cookies. Each account is isolated at the query layer. Only admins can create users or change strategy enablement. Discord webhook and SMTP secrets belong in environment variables, not client components. The exposed Compose web port binds to 127.0.0.1; place TLS/reverse proxy in front of it for remote use, and back up PostgreSQL.

Before monetising: verify spreadsheet parity independently, secure appropriate market-data licensing, add per-user registration/recovery, subscriptions, permissions testing, account suitability/eligibility evidence, error monitoring, backup-restore drills and legal/privacy review. Stripe is *not* configured.

**Not financial advice.**
