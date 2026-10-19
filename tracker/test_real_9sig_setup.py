"""Former 9Sig/GIA enrolment URL must forward to the single ISA-only flow."""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.models import Account, LedgerEntry, StrategyInstance, StrategyPreset


class LegacyEnrollmentRoutingTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            "former_enrollment_owner", password="a-long-test-password")
        self.legacy_url = reverse("start_real_9sig")
        self.editor_url = reverse("new_strategy")
        self.modern_url = reverse("cockpit_setup")
        self.old_payload = {
            "account_name": "Custom Trading 212 GIA",
            "account_kind": "GIA",
            "strategy_name": "Owner edited rules",
            "start_date": str(timezone.localdate() - timedelta(days=4)),
            "starting_cash": "1000",
            "monthly_target": "500",
            "strategy_slug": "nasdaq-quarterly-target",
            "country": "GB",
            "broker": "TRADING212",
            "confirmed_broker_availability": "on",
        }

    def test_legacy_entrypoints_require_signin(self):
        self.assertEqual(self.client.get(self.legacy_url).status_code, 302)
        self.assertEqual(self.client.post(self.editor_url, self.old_payload).status_code, 302)
        self.assertFalse(Account.objects.exists())

    def test_get_routes_both_old_enrollment_pages_to_new_owner_setup(self):
        self.client.force_login(self.owner)
        self.assertRedirects(self.client.get(self.legacy_url), self.modern_url)
        self.assertRedirects(self.client.get(self.editor_url), self.modern_url)
        page = self.client.get(self.modern_url)
        self.assertContains(page, "LQQ3")
        self.assertContains(page, "ERNS")
        self.assertNotContains(page, 'value="GIA"')

    def test_legacy_post_never_creates_a_gia_or_invents_cash(self):
        self.client.force_login(self.owner)
        for url in (self.legacy_url, self.editor_url):
            response = self.client.post(url, self.old_payload)
            self.assertRedirects(response, self.modern_url)
        self.assertFalse(Account.objects.filter(owner=self.owner).exists())
        self.assertFalse(StrategyInstance.objects.filter(owner=self.owner).exists())
        self.assertFalse(LedgerEntry.objects.exists())

    def test_canonical_setup_keeps_actual_funding_and_pins_identity(self):
        self.client.force_login(self.owner)
        date = timezone.localdate() - timedelta(days=4)
        data = {
            "position_mode": "FRESH", "starting_cash": "1000",
            "start_date": date.isoformat(),
            "monthly_target": "500", "destination": "NONE", "email": "",
            # Attempting to supply old-style mutable fields has no effect.
            "account_kind": "GIA", "equity_symbol": "TQQQ", "safe_symbol": "AGG",
        }
        response = self.client.post(self.modern_url, data)
        self.assertRedirects(response, reverse("cockpit_today"))
        portfolio = StrategyInstance.objects.get(owner=self.owner)
        self.assertEqual(portfolio.account.kind, "ISA")
        self.assertEqual(portfolio.equity_symbol, "LQQ3.L")
        self.assertEqual(portfolio.safe_symbol, "ERNS.L")
        self.assertTrue(portfolio.mapping_version)
        self.assertTrue(portfolio.strategy_version)
        self.assertEqual(portfolio.ledger.count(), 1)
        self.assertEqual(portfolio.ledger.get().amount, D("1000"))
        self.assertEqual(portfolio.ledger.get().occurred_on, date)
