"""Paper trial is virtual, isolated, idempotent and never sends broker orders."""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.models import Account, LedgerEntry, StrategyInstance, StrategyValueSnapshot
from tracker.paper import EQUITY, SAFE, PAPER_NAME, PaperSetupUnavailable
from tracker.services import ledger_summary, consolidated


class PaperTrialTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="paper-owner", email="paper-owner@example.com",
            password="long-paper-test-password")
        self.other = get_user_model().objects.create_user(
            username="paper-other", email="paper-other@example.com",
            password="long-paper-test-password")
        self.url = reverse("start_paper_trial")
        self.payload = {"confirm_paper": "PAPER_ONLY"}
        self.day = timezone.localdate() - timedelta(days=1)

    def quote(self, symbol):
        price = Decimal("12.50") if symbol == EQUITY else Decimal("100.00")
        return SimpleNamespace(symbol=symbol, price=price, currency="GBP", as_of=self.day)

    def test_user_must_be_signed_in_and_post_with_confirmation(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 302)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.post(self.url, {"confirm_paper": "NOT_CONFIRMED"})
        self.assertFalse(Account.objects.filter(owner=self.owner).exists())

    @patch("tracker.paper.sync_ticker")
    def test_virtual_1000_paper_seed_and_no_second_portfolio(self, sync):
        sync.side_effect = self.quote
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 302)
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 302)
        strategy = StrategyInstance.objects.get(owner=self.owner, name=PAPER_NAME)
        self.assertEqual(strategy.account.kind, "PAPER")
        self.assertTrue(strategy.market_data_enabled)
        self.assertFalse(strategy.contribution_alerts)
        self.assertEqual(strategy.monthly_target, Decimal("500.00"))
        self.assertEqual(strategy.equity_symbol, EQUITY)
        self.assertEqual(strategy.safe_symbol, SAFE)
        self.assertEqual(Account.objects.filter(owner=self.owner, kind="PAPER").count(), 1)
        self.assertEqual(StrategyInstance.objects.filter(owner=self.owner).count(), 1)
        ledger = list(strategy.ledger.order_by("pk"))
        self.assertEqual([entry.kind for entry in ledger],
                         ["DEPOSIT", "BUY", "BUY"])
        self.assertTrue(all(entry.occurred_on == self.day for entry in ledger))
        self.assertTrue(all("PAPER ONLY" in entry.note for entry in ledger))
        self.assertEqual(ledger[0].amount, Decimal("1000.00"))
        report = ledger_summary(strategy)
        self.assertEqual(report["cash"], Decimal("0.00"))
        self.assertEqual(report["value"], Decimal("1000.00"))
        self.assertEqual(set(report["positions"]), {EQUITY, SAFE})
        self.assertEqual(StrategyValueSnapshot.objects.filter(strategy=strategy).count(), 1)
        self.assertEqual(strategy.prices.count(), 2)
        self.assertTrue(all(x.source == "yahoo" for x in strategy.prices.all()))
        self.assertEqual(sync.call_count, 2)
        real = consolidated(self.owner)
        self.assertEqual(real["total"], Decimal("0.00"))
        self.assertEqual(real["net_contributed"], Decimal("0.00"))
        self.assertEqual(len(real["items"]), 0)

    @patch("tracker.paper.sync_ticker")
    def test_unavailable_quote_does_not_create_partial_virtual_portfolio(self, sync):
        sync.side_effect = lambda ticker: None
        self.client.force_login(self.owner)
        response = self.client.post(self.url, self.payload)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Account.objects.filter(owner=self.owner).exists())
        self.assertFalse(StrategyInstance.objects.filter(owner=self.owner).exists())

    @patch("tracker.paper.sync_ticker")
    def test_quote_day_mismatch_refuses_to_fake_synchronous_fill(self, sync):
        def different_days(symbol):
            mark = self.quote(symbol)
            if symbol == SAFE:
                mark.as_of -= timedelta(days=1)
            return mark
        sync.side_effect = different_days
        self.client.force_login(self.owner)
        self.client.post(self.url, self.payload)
        self.assertFalse(Account.objects.filter(owner=self.owner).exists())

    @patch("tracker.paper.sync_ticker")
    def test_paper_is_owned_and_hidden_from_another_user(self, sync):
        sync.side_effect = self.quote
        self.client.force_login(self.owner)
        self.client.post(self.url, self.payload)
        first = StrategyInstance.objects.get(owner=self.owner)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("strategy_detail", args=[first.pk])).status_code, 404)
        self.assertEqual(StrategyInstance.objects.filter(owner=self.other).count(), 0)

    @patch("tracker.paper.sync_ticker")
    def test_unverified_hourly_quotes_cannot_invent_virtual_strategy_snapshot(self, sync):
        from django.core.management import call_command
        from tracker.models import PriceMark, WealthSnapshot
        sync.side_effect = self.quote
        self.client.force_login(self.owner)
        self.client.post(self.url, self.payload)
        strategy = StrategyInstance.objects.get(owner=self.owner)
        today = timezone.localdate()
        PriceMark.objects.filter(strategy=strategy).update(as_of=today)
        PriceMark.objects.filter(strategy=strategy, symbol=EQUITY).update(
            price=Decimal("13.50"))
        with patch("tracker.management.commands.sync_market_and_signals.review_strategy",
                   return_value={"status": "waiting for first completed quarter"}):
            call_command("sync_market_and_signals", verbosity=0)
        rows = list(StrategyValueSnapshot.objects.filter(strategy=strategy))
        # A manually modified PriceMark is not a genuine market observation.
        # The newer scheduler must NOT invent a second price-verified value.
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].as_of, self.day)
        self.assertFalse(StrategyValueSnapshot.objects.filter(
            strategy=strategy, as_of=today).exists())
        self.assertFalse(WealthSnapshot.objects.filter(owner=self.owner).exists())
