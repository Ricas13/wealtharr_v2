"""Real 9Sig startup only records actual cash. Never fabricate broker fills."""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.models import Account, LedgerEntry, StrategyInstance, StrategyPreset
from tracker.services import consolidated


class RealNineSigSetupTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            "real-owner", email="real-owner@example.com",
            password="long-test-owner-password")
        self.other = get_user_model().objects.create_user(
            "real-other", password="long-test-owner-password")
        self.url = reverse("start_real_9sig")
        self.data = {
            "account_name": "Trading 212 ISA",
            "account_kind": "ISA",
            "strategy_name": "9Sig - real",
            "start_date": str(timezone.localdate() - timedelta(days=10)),
            "starting_cash": "1000.00",
            "monthly_target": "500.00",
            "equity_symbol": " lqq3.l ",
            "safe_symbol": " erns.l ",
        }

    def test_owner_must_sign_in(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.client.post(self.url, self.data).status_code, 302)
        self.assertFalse(Account.objects.exists())

    def test_real_portfolio_only_adds_actual_deposit_and_no_buys(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.url, self.data)
        self.assertEqual(response.status_code, 302)
        strategy = StrategyInstance.objects.get(owner=self.owner)
        self.assertEqual(strategy.account.kind, "ISA")
        self.assertEqual(strategy.account.name, "Trading 212 ISA")
        self.assertTrue(strategy.market_data_enabled)
        self.assertEqual(strategy.equity_symbol, "LQQ3.L")
        self.assertEqual(strategy.safe_symbol, "ERNS.L")
        self.assertEqual(strategy.monthly_target, D("500"))
        entries = list(strategy.ledger.all())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].kind, LedgerEntry.DEPOSIT)
        self.assertEqual(entries[0].amount, D("1000.00"))
        self.assertEqual(entries[0].occurred_on, timezone.localdate()-timedelta(days=10))
        self.assertEqual(strategy.prices.count(), 0)
        self.assertEqual(consolidated(self.owner)["total"], D("1000.00"))
        self.assertEqual(strategy.ledger.filter(kind=LedgerEntry.BUY).count(), 0)
        self.assertEqual(StrategyInstance.objects.filter(owner=self.other).count(), 0)
        self.assertEqual(self.client.get(reverse("strategy_detail", args=[strategy.pk])).status_code, 200)

    def test_duplicate_strategy_name_does_not_duplicate_cash_deposits(self):
        self.client.force_login(self.owner)
        self.client.post(self.url, self.data)
        second = self.client.post(self.url, self.data)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(StrategyInstance.objects.count(), 1)
        self.assertEqual(LedgerEntry.objects.count(), 1)

    def test_form_cannot_use_future_date_or_same_symbol(self):
        self.client.force_login(self.owner)
        bad = self.data.copy()
        bad["start_date"] = str(timezone.localdate()+timedelta(days=2))
        bad["safe_symbol"] = "LQQ3.L"
        response = self.client.post(self.url, bad)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "future cash deposit")
        self.assertFalse(Account.objects.exists())

    def test_existing_account_is_reused_without_leaking_paper(self):
        self.client.force_login(self.owner)
        real = Account.objects.create(owner=self.owner, name="Trading 212 ISA", kind="ISA")
        self.client.post(self.url, self.data)
        strategy = StrategyInstance.objects.get(owner=self.owner)
        self.assertEqual(strategy.account_id, real.pk)
        self.assertEqual(Account.objects.filter(owner=self.owner).count(), 1)

    def test_strategy_must_be_enabled(self):
        self.client.force_login(self.owner)
        preset = StrategyPreset.objects.get(slug="nasdaq-quarterly-target")
        preset.active = False
        preset.save()
        response = self.client.post(self.url, self.data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(StrategyInstance.objects.exists())
        self.assertFalse(Account.objects.exists())
