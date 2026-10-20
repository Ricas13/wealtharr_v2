"""Old onboarding links are GET-only bookmarks; all writes use the v2 wizard."""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.models import Account, LedgerEntry, StrategyInstance


class LegacyEnrollmentRoutingTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            "former_enrollment_owner", password="a-long-test-password")
        self.legacy_url = reverse("start_real_9sig")
        self.editor_url = reverse("new_strategy")
        self.modern_url = reverse("v2_add_strategy")
        self.old_payload = {
            "account_name": "Custom Trading 212 GIA", "account_kind": "GIA",
            "strategy_name": "Owner edited rules",
            "start_date": str(timezone.localdate() - timedelta(days=4)),
            "starting_cash": "1000", "monthly_target": "500",
            "strategy_slug": "nasdaq-quarterly-target", "broker": "TRADING212",
        }

    def test_legacy_entrypoints_require_signin(self):
        self.assertEqual(self.client.get(self.legacy_url).status_code, 302)
        self.assertEqual(self.client.post(self.editor_url, self.old_payload).status_code, 302)
        self.assertFalse(Account.objects.exists())

    def test_get_routes_both_old_enrollment_pages_to_new_owner_setup(self):
        self.client.force_login(self.owner)
        self.assertRedirects(self.client.get(self.legacy_url), self.modern_url, status_code=301)
        self.assertRedirects(self.client.get(self.editor_url), self.modern_url, status_code=301)
        page = self.client.get(reverse("v2_catalogue"))
        self.assertContains(page, "LQQ3")
        self.assertContains(page, "ERNS")
        self.assertEqual(self.client.get(self.modern_url).status_code, 200)

    def test_legacy_post_never_creates_a_gia_or_invents_cash(self):
        self.client.force_login(self.owner)
        for url in (self.legacy_url, self.editor_url):
            response = self.client.post(url, self.old_payload)
            self.assertEqual(response.status_code, 405)
        self.assertFalse(Account.objects.filter(owner=self.owner).exists())
        self.assertFalse(StrategyInstance.objects.filter(owner=self.owner).exists())
        self.assertFalse(LedgerEntry.objects.exists())

    def test_canonical_setup_keeps_actual_funding_and_pins_identity(self):
        self.client.force_login(self.owner)
        day = timezone.localdate() - timedelta(days=4)
        url = self.modern_url
        self.client.get(url + "?restart=1")
        self.client.post(url, {"step": "1", "strategy": "nasdaq-quarterly-target"})
        self.client.post(url, {
            "step": "2", "platform": "TRADING212", "account_type": "ISA",
        })
        self.client.post(url, {"step": "3", "ack": "yes"})
        preview = self.client.post(url, {
            "step": "4", "mode": "fresh", "name": "My 9Sig ISA",
            "start": day.isoformat(), "amount": "1000",
            "deposited": "yes",
            # Forged user-modifiable identity and rules never override released code.
            "account_kind": "GIA", "equity_symbol": "TQQQ", "safe_symbol": "AGG",
        })
        self.assertContains(preview, "step 5 of 5")
        response = self.client.post(url, {"step": "5"})
        portfolio = StrategyInstance.objects.get(owner=self.owner)
        self.assertRedirects(response, reverse(
            "v2_instance_page", args=[portfolio.pk, "today"]))
        self.assertEqual(portfolio.account.kind, "ISA")
        self.assertEqual(portfolio.equity_symbol, "LQQ3.L")
        self.assertEqual(portfolio.safe_symbol, "ERNS.L")
        self.assertTrue(portfolio.mapping_version)
        self.assertTrue(portfolio.strategy_version)
        self.assertTrue(portfolio.tracking_only)
        self.assertEqual(portfolio.ledger.count(), 1)
        self.assertEqual(portfolio.ledger.get().amount, D("1000"))
        self.assertEqual(portfolio.ledger.get().occurred_on, day)
