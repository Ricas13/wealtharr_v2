# RESUME HERE — Wealtharr personal 9Sig cockpit

Repository: `Ricas13/wealtharr_v2`  
Working branch: `feature/owner-9sig-cockpit-resumable`  
Pull request: https://github.com/Ricas13/wealtharr_v2/pull/11  
Base: `main` (at start of work: `628a22f2b08930c36549bcaf4c61e4584dee1949`)  

## Objective

Make Wealtharr a single-owner 9Sig investment cockpit: Today (value, contributed, gains, a reliable next action), History, Ledger, Settings, one guided manual rebalance, Trading 212 CSV preview/import, one-screen setup, money-weighted returns, concise reminders. Do not place broker orders or invent prices. Existing append-only ledger, immutable reset baseline, and hard refusals must remain intact.

## State / checkpoints

- [x] Dedicated new branch created; `main` deliberately untouched.
- [x] Added all Django cockpit modules, templates, CSS and JS from the reviewed reference patch.
- [x] Added additive `RebalanceExecution` and `BrokerImportRow` models and migration `0009`.
- [x] Wired `/cockpit/`, set Today as authenticated landing, simplified navigation, preserved legacy URLs.
- [x] Prevented duplicate immediate quarterly notification delivery; one concise digest scheduler replaces legacy monthly sender.
- [x] Source Python compile/syntax checks and standalone Decimal examples in authoring environment.
- [x] CSV event-ID collision protection and mixed-timezone parsing regression coverage.
- [x] Optional Trading 212 read-only API security/rollout decision note.
- [ ] GitHub Actions was **queued** at the last check; GitHub CI: `python manage.py check`, `makemigrations --check --dry-run`, migrate, tests, collectstatic. **Inspect live CI results and fix until green.**
- [ ] Run dry-run CSV preview with a REAL, redacted Trading 212 export; ensure currency, fees, splits/dividends and ISIN mapping match broker records.
- [ ] Validate every quarter against owner's Jason Kelly workbook (see `docs/FINANCIAL_QA.md`); current signal engine documents unverified discretionary branches.
- [ ] Manual in-browser mobile/accessibility smoke and notifications test via configured destination.
- [ ] Backup/restore verification then deploy to Oracle only with authorized infrastructure access. DO NOT assume this branch is live.

## First commands in a NEW CHAT

1. Open the repo and branch: `https://github.com/Ricas13/wealtharr_v2/tree/feature/owner-9sig-cockpit-resumable`.
2. Read this file, `docs/INSTALL.md` and `docs/FINANCIAL_QA.md` plus latest PR and CI statuses.
3. Fix failing checks. Do not start a second competing branch unless the PR has been merged.
4. Use this branch and its PR for all further commits; update this checklist after each meaningful step. Treat unmarked checks as OPEN, not silently complete.
5. Test: `DEBUG=1 SECRET_KEY=test-only python manage.py check; python manage.py makemigrations --check --dry-run; python manage.py test tracker; python manage.py collectstatic --noinput`.
6. Follow migration and deployment instructions in `docs/INSTALL.md`. Keep one scheduler replica, do not run old and new notification commands together.

## Critical financial semantics

- Do not equate LQQ3/ERNS with TQQQ/AGG; instruments have different tracking, resets, fees and FX.
- Actual executions require owner confirmation; ordinary quarterly trades never generate a new 60/40 reset baseline.
- New 60/40 baseline needs actual fills, verified later close, GBP instruments, >=0 cash and equity within 5pp allocation.
- Trading 212 CSV import is intentionally conservative and atomic: preview rejects unmapped/FX/uncertain rows.
- XIRR is money-weighted, not time-weighted. Exact TWR/drawdown withheld absent flow-boundary unitisation.
- The spike reference and discretionary 9Sig exceptions still need comparison to owner's workbook. Do not label the rules “verified” until independently confirmed.

## User prompt for a new chat

> Continue `Ricas13/wealtharr_v2` on branch `feature/owner-9sig-cockpit-resumable` using `docs/RESUME.md`. Read the PR and CI, fix every outstanding test/migration/flow defect, verify the 9Sig safety checklist, and update the resume file. Do not start over or replace the append-only ledger. Work as far as possible in this chat and leave the branch and PR in a resumable state.
