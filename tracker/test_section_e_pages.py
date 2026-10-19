"""Section E: live owner-scoped pages and conservative presentation semantics."""
import csv
from datetime import date
from decimal import Decimal as D
from io import StringIO

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from tracker.core.presentation import allocation_comparison, select_next_step
from tracker.models import Account, LedgerEntry, StrategyInstance, StrategyPreset
from tracker.services import validate_and_record


class SectionEPageTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="section-e-owner", password="test-only-password")
        self.other = get_user_model().objects.create_user(
            username="section-e-other", password="test-only-password")
        self.preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "Admin editable title", "risk": "HIGH",
                      "summary": "Mutable summary", "methodology": "MUTABLE ADMIN RULE TEXT"})
        self.account = Account.objects.create(
            owner=self.owner, name="Owner ISA", kind="ISA",
            country="GB", broker="TRADING212")
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=self.account, preset=self.preset,
            name="My Portfolio", market_data_enabled=True,
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L")
        self.other_account = Account.objects.create(
            owner=self.other, name="Second ISA", kind="ISA",
            country="GB", broker="TRADING212")
        self.other_strategy = StrategyInstance.objects.create(
            owner=self.other, account=self.other_account, preset=self.preset,
            name="Other account", market_data_enabled=True,
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L")
        self.client.force_login(self.owner)

    def deposit(self, strategy, amount, when):
        validate_and_record(strategy, LedgerEntry(
            kind="DEPOSIT", occurred_on=when, amount=D(amount)))

    def test_today_has_one_safe_next_action_and_no_guessed_return(self):
        self.deposit(self.strategy, "200", date(2026, 2, 3))
        response = self.client.get(reverse("cockpit_today"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Next action", count=1)
        self.assertContains(response, "Actual deposit date")
        self.assertContains(response, "Record actual contribution")
        self.assertContains(response, "£200.00")
        self.assertNotContains(response, "money-weighted equivalent")
        self.assertNotContains(response, "Review and record what I did")
        self.assertContains(response, 'aria-current="page"')

    def test_strategy_rules_source_is_fixed_not_editable_preset(self):
        response = self.client.get(reverse("cockpit_strategy"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "9% each completed quarter")
        self.assertContains(response, "IE00BLRPRL42")
        self.assertContains(response, "Issuer says eligible")
        self.assertContains(response, "Not verified")
        self.assertNotContains(response, "MUTABLE ADMIN RULE TEXT")

    def test_ledger_filter_and_full_csv_export_are_user_isolated(self):
        self.deposit(self.strategy, "500", date(2026, 2, 3))
        self.deposit(self.other_strategy, "9999", date(2026, 2, 3))
        validate_and_record(self.strategy, LedgerEntry(
            kind="BUY", symbol="LQQ3.L", occurred_on=date(2026, 2, 4),
            quantity=D("2"), unit_price=D("100"), fee=D("1.25"),
            note="=SUM(1,2)"))
        url = reverse("cockpit_ledger")
        response = self.client.get(url, {"kind": "BUY", "order": "oldest"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "LQQ3.L")
        self.assertNotContains(response, "9,999")
        self.assertContains(response, "Export matching CSV")
        csv_response = self.client.get(url, {"kind": "BUY", "format": "csv"})
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn("attachment;", csv_response["Content-Disposition"])
        parsed = list(csv.DictReader(StringIO(csv_response.content.decode())))
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["kind"], "BUY")
        self.assertEqual(parsed[0]["unit_price_gbp"], "100.00000000")
        self.assertEqual(parsed[0]["fee_gbp"], "1.25")
        self.assertEqual(parsed[0]["note"], "'=SUM(1,2)")
        all_rows = list(csv.DictReader(StringIO(
            self.client.get(url, {"format": "csv"}).content.decode())))
        self.assertEqual(len(all_rows), 2)
        self.assertNotIn("9999", str(all_rows))

    def test_history_never_links_to_execution_of_old_signal(self):
        self.deposit(self.strategy, "100", date(2026, 2, 3))
        response = self.client.get(reverse("cockpit_history"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Portfolio value over time")
        self.assertContains(response, "Money-weighted return")
        self.assertContains(response, "No verified valuation snapshots yet")
        self.assertNotContains(response, "MUTABLE ADMIN RULE TEXT")

    def test_unsupported_profile_never_infers_isa_ticker(self):
        self.account.broker = "UNKNOWN"
        self.account.save(update_fields=["broker"])
        response = self.client.get(reverse("cockpit_strategy"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not mapped")
        self.assertNotContains(response, "IE00BLRPRL42")

    def test_other_owner_cannot_view_portfolio_on_any_page(self):
        self.deposit(self.strategy, "345", date(2026, 2, 3))
        self.client.force_login(self.other)
        for name in ("cockpit_today", "cockpit_history", "cockpit_ledger",
                     "cockpit_strategy"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "£345.00")
            self.assertNotContains(response, "Owner ISA")
        response = self.client.get(reverse("cockpit_ledger"), {"format": "csv"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("345.00", response.content.decode())

    def test_no_uncertain_allocation_and_manual_warning_never_trade(self):
        not_ready = select_next_step(
            signal_kind="SPIKE_WATCH", signal_title="Warning",
            signal_message="Check reference", proposed_actions=(
                {"side": "BUY", "symbol": "LQQ3.L"},),
            price_ready=False, unavailable_reason="Stale",
            next_review_label="31 Dec")
        self.assertEqual(not_ready.state, "review")
        self.assertIsNone(allocation_comparison({
            "cash": D("100"), "value": D("200"),
            "positions": {"UNMAPPED.L": D("1")}, "marks": {}},
            instrument_roles={"LQQ3.L": "growth", "ERNS.L": "safe"},
            fixed_targets={"growth": D("0.6"), "safe": D("0.4")},
            verified=True))
