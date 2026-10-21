"""Only actual executed fills and verified GBP quotes can start a new 9Sig cycle."""
from datetime import timedelta
from decimal import Decimal as D
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from tracker.models import (Account, LedgerEntry, PriceMark, StrategyBaseline,
                            StrategyInstance, StrategyPreset, StrategySignal)
from tracker.services import validate_and_record


class ResetBaselineTests(TestCase):
    def setUp(self):
        U = get_user_model()
        self.owner = U.objects.create_user("reset-owner", "reset@example.com", "long-reset-password-test")
        self.other = U.objects.create_user("reset-other", "other@example.com", "long-reset-password-test")
        account = Account.objects.create(owner=self.owner, name="ISA reset", kind="ISA")
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=account,
            preset=StrategyPreset.objects.get(slug="nasdaq-quarterly-target"),
            name="9Sig ISA", market_data_enabled=True,
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L")
        self.signal = StrategySignal.objects.create(
            strategy=self.strategy, signal_key="quarter:2026-Q3",
            kind="QUARTERLY",
            title="Quarterly reset", as_of=timezone.localdate()-timedelta(days=25),
            message="Eight consecutive quarters without a sale: review a reset to 60% equity.",
            actions=[], acknowledged_at=timezone.now())
        # The retired reset endpoint no longer exists; posting to it does nothing.
        self.url = "/app/strategies/%s/rebaseline/" % self.strategy.pk
        self.client.force_login(self.owner)

    def trades_and_quotes(self):
        trade_day = timezone.localdate() - timedelta(days=3)
        validate_and_record(self.strategy, LedgerEntry(
            kind="DEPOSIT", occurred_on=trade_day, amount=D("1000")))
        validate_and_record(self.strategy, LedgerEntry(
            kind="BUY", occurred_on=trade_day, symbol="LQQ3.L",
            quantity=D("6"), unit_price=D("100")))
        validate_and_record(self.strategy, LedgerEntry(
            kind="BUY", occurred_on=trade_day, symbol="ERNS.L",
            quantity=D("4"), unit_price=D("100")))
        quote_date = timezone.localdate() - timedelta(days=1)
        PriceMark.objects.create(strategy=self.strategy, symbol="LQQ3.L",
                                 price=D("100"), as_of=quote_date, source="yahoo")
        PriceMark.objects.create(strategy=self.strategy, symbol="ERNS.L",
                                 price=D("100"), as_of=quote_date, source="yahoo")

    def test_reset_is_not_confirmed_before_any_actual_trades(self):
        response = self.client.post(self.url, {"confirm": "RESET"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(StrategyBaseline.objects.filter(strategy=self.strategy).exists())

    def test_cross_user_cannot_reset_another_owners_strategy(self):
        self.trades_and_quotes()
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(self.url, {"confirm": "RESET"}).status_code, 404)
        self.assertFalse(StrategyBaseline.objects.filter(strategy=self.strategy).exists())

    def test_retired_text_only_reset_does_not_create_baseline(self):
        self.trades_and_quotes()
        response = self.client.post(self.url, {"confirm": "RESET"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(StrategyBaseline.objects.filter(strategy=self.strategy).exists())

    def test_unreviewed_reset_cannot_be_confirmed(self):
        self.trades_and_quotes()
        self.signal.acknowledged_at = None
        self.signal.save(update_fields=["acknowledged_at"])
        self.assertEqual(self.client.post(self.url, {"confirm": "RESET"}).status_code, 404)
        self.assertFalse(StrategyBaseline.objects.filter(strategy=self.strategy).exists())

    def test_provisional_spike_never_anchors_from_text_or_unlinked_trades(self):
        self.trades_and_quotes()
        StrategySignal.objects.create(
            strategy=self.strategy, signal_key="spike:2026-Q4",
            kind="SPIKE_WATCH",
            title="Possible TQQQ spike",
            as_of=timezone.localdate() - timedelta(days=4),
            message="possible 100% spike: review a reset to 60% equity / 40% safe",
            actions=[], acknowledged_at=timezone.now())
        response = self.client.post(self.url, {"confirm": "RESET"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(StrategyBaseline.objects.filter(strategy=self.strategy).exists())

    def test_rejected_spike_cannot_anchor_a_reset(self):
        self.trades_and_quotes()
        spike = StrategySignal.objects.create(
            strategy=self.strategy, signal_key="spike:rejected",
            kind="SPIKE_WATCH", title="Not correct",
            as_of=timezone.localdate() - timedelta(days=4),
            message="possible 100% spike: review a reset to 60% equity",
            actions=[], acknowledged_at=timezone.now(),
            review_outcome="FALSE_POSITIVE", review_note="reference was wrong")
        self.signal.acknowledged_at = None
        self.signal.save(update_fields=["acknowledged_at"])
        self.assertEqual(self.client.post(self.url, {"confirm": "RESET"}).status_code, 404)
        self.assertFalse(StrategyBaseline.objects.exists())
