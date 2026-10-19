# Integration into Ricas13/wealtharr_v2 (Django 5.2)

**STATUS: Integrated as a dedicated GitHub feature branch. Await CI, real Trading 212 CSV reconciliation and owner 9Sig spreadsheet parity before relying on trade amounts.**

1. The files are committed on `feature/owner-9sig-cockpit-resumable`; preserve a production PostgreSQL backup and test restore before merging or deploying.
2. The two additive models are already appended to `tracker/models.py`; migration `0009_owner_cockpit.py` is included. Review and apply using `python manage.py migrate` after backup.
3. The `/cockpit/` routes are already mounted; authenticated homepage/login redirects to Today. The old `/app/` legacy Overview still exists for old bookmarks.
4. The shell already includes `cockpit.css` and the cockpit navigation (Today / History / Ledger / Settings); no further base template change is necessary. Previously:
   `<link rel="stylesheet" href="{% static 'css/cockpit.css' %}">`
   Change the sidebar to Today/History/Ledger/Settings, e.g. URL names `cockpit_today`, `cockpit_history`, `cockpit_ledger`, `user_settings`. Move Admin under a collapsed `<details>` in Settings. The current `site.js` handles the existing chart container IDs on Today without another JS library. Add `<script defer src="{% static 'js/cockpit.js' %}"></script>` for the Check Prices loading state.
5. The market_signals alert dispatch guard is already adjusted. Previously:
   `if actionable:`
   with:
   `if actionable and alert["kind"] == "THIRTY_DOWN":`
   so ordinary quarterly signals are sent once by the digest job instead of instantly **and again** in the digest. Keep `_alert_intraperiod_spike` unchanged for urgent spike warnings. Existing delivery keys won't be deleted.
6. The Compose reminders job is already changed from `send_due_notifications` to `send_cockpit_notifications` **once**. Keep `python manage.py sync_market_and_signals` hourly and one scheduler replica. Never run old and new reminder commands together. New command itself retries failed deliveries.
7. Run `python manage.py check`, `python manage.py makemigrations --check --dry-run`, `python manage.py test tracker`, then test with a cloned production database and mock Trading 212 export. Check new tests before deploying. Never seed fake prices into production.
8. The GET form for onboarding records real cash only; for existing holdings, use the full CSV import and reconcile. For notifications, keep your current SystemIntegration configuration until simplified settings are wired. The quick deposit form records a real deposit only after the owner actually makes it.

## Deployment hold points

- Validate spreadsheet parity for 9% quarter growth, half contributions (end of quarter), 30 Down state, 81% throttle, 100% spike price reference, and baseline reset.
- **Important:** The current StrategySignal.actions JSON carries estimated size and GBP based on a quarter-end close, not executable broker orders. No exact live trade price can be guaranteed.
- **Important:** Confirming post-quarter trade fills in the new flow only works once post-fill Yahoo prices are present. Otherwise it rolls back and explains why. This is intentional: no fabricated baseline.
- Ordinary quarterly fills never create a new StrategyBaseline; doing so would reset the 9% line incorrectly. Only a genuine 60/40 reset does.
- The fee/currency fields in actual Trading 212 CSV exports vary. Preview and reconcile against a known real export before enabling writes; do not reinterpret other currencies.
- This patch intentionally keeps all existing SQL schema until additive migration is reviewed. It does not connect to the Trading 212 API.

## Schema notes

`RebalanceExecution.signal` OneToOne provides a race-safe completion marker. This is separate from legacy `acknowledged_at`: a legacy acknowledged signal is **not proof of execution**. `BrokerImportRow` stores the imported event fingerprint and source LedgerEntry; the original event remains append-only.
