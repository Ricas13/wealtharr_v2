"""Section E page routing, wording and owner-isolated presentation tests."""
from datetime import date
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.models import (
    Account, LedgerEntry, StrategyInstance, StrategyPreset, StrategySignal,
)
from tracker.services import validate_and_record


class OwnerPageTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            "page_owner", password="example-only-strong-password")
        self.account = Account.objects.create(
            owner=self.owner, name="Personal ISA", kind="ISA",
            broker="TRADING212", country="GB")
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "9Sig", "summary": "Follow recorded quarterly reviews.",
                      "risk": "HIGH", "methodology": "Fixed quarterly method."})
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=self.account, preset=preset,
            name="My Portfolio", equity_symbol="LQQ3.L", safe_symbol="ERNS.L",
            market_data_enabled=True, monthly_target=D("500"))
        self.client.force_login(self.owner)

    def add_cash(self, day=None):
        validate_and_record(self.strategy, LedgerEntry(
            kind="DEPOSIT", occurred_on=day or timezone.localdate(),
            amount=D("1000")))

    def test_today_leads_with_one_action_and_recognises_actual_cash(self):
        self.add_cash()
        response = self.client.get(reverse("cockpit_today"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "What to do next")
        self.assertContains(response, "1,000.00")
        self.assertContains(response, "Add your actual purchases")
        self.assertContains(response, "Actual deposit date")
        self.assertNotContains(response, "money-weighted equivalent")
        self.assertContains(response, reverse("cockpit_strategy"))

    def test_history_only_displays_this_owners_reviews_as_past_records(self):
        self.add_cash()
        StrategySignal.objects.create(
            strategy=self.strategy, signal_key="quarter:own:1",
            kind="QUARTERLY", title="My quarterly review",
            as_of=date(2026, 6, 30),
            message="Old instruction, history only",
            actions=[{"side": "BUY", "symbol": "LQQ3.L",
                      "quantity": "1", "estimated_gbp": "100"}])
        stranger = get_user_model().objects.create_user(
            "other_page_owner", password="example-only-strong-password")
        other_account = Account.objects.create(
            owner=stranger, name="Second ISA", kind="ISA", broker="TRADING212")
        other = StrategyInstance.objects.create(
            owner=stranger, account=other_account,
            preset=self.strategy.preset, name="Someone else's strategy")
        StrategySignal.objects.create(
            strategy=other, signal_key="quarter:other:1",
            kind="QUARTERLY", as_of=date(2026, 6, 30),
            title="Other owner's private signal", message="Private",
            actions=[])
        response = self.client.get(reverse("cockpit_history"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My quarterly review")
        self.assertContains(response, "historical review is not permission")
        self.assertNotContains(response, "Other owner's private signal")
        self.assertNotContains(response, reverse("cockpit_rebalance", args=[
            StrategySignal.objects.get(strategy=self.strategy).pk]))
        self.assertContains(response, "Money-weighted return")

    def test_ledger_displays_recorded_cash_cost_including_fee(self):
        funding_day = date(2026, 2, 1)
        self.add_cash(funding_day)
        validate_and_record(self.strategy, LedgerEntry(
            kind="BUY", occurred_on=funding_day, symbol="LQQ3.L",
            quantity=D("2"), unit_price=D("100"), fee=D("1.25")))
        response = self.client.get(reverse("cockpit_ledger"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "-201.25")
        self.assertContains(response, "798.75")
        self.assertContains(response, "LQQ3.L")
        self.assertContains(response, "Import Trading 212 CSV")
        self.assertContains(response, "Cash change")

    def test_strategy_shows_exact_identity_without_claiming_personal_isa_approval(self):
        response = self.client.get(reverse("cockpit_strategy"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "IE00BLRPRL42")
        self.assertContains(response, "IE00BCRY6441")
        self.assertContains(response, "GBX")
        self.assertContains(response, "not proof of availability")

    def test_unmapped_account_displays_no_guessed_instruments(self):
        self.account.broker = "UNKNOWN"
        self.account.save(update_fields=["broker"])
        response = self.client.get(reverse("cockpit_strategy"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not mapped for this account")
        self.assertNotContains(response, "IE00BLRPRL42")

    def test_all_four_owner_pages_require_authentication(self):
        self.client.logout()
        for name in ("cockpit_today", "cockpit_history",
                     "cockpit_ledger", "cockpit_strategy"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302)
            self.assertIn("login", response["Location"])
