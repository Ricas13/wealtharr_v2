"""Owner data integrity tests for scheduled daily portfolio history."""
from datetime import timedelta
from decimal import Decimal as D
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from tracker.models import Account, LedgerEntry, MarketObservation, PriceMark, StrategyInstance, StrategyPreset, StrategyValueSnapshot
from tracker.services import validate_and_record


class SnapshotSafetyTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            "snapshot-owner", "snapshot@example.com", "a-long-password-for-testing")
        account = Account.objects.create(owner=self.owner, name="ISA", kind="ISA", broker="TRADING212", country="GB")
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=account,
            preset=StrategyPreset.objects.get(slug="nasdaq-quarterly-target"),
            name="Monitored ISA", market_data_enabled=True,
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L")
        self.quoted_day = timezone.localdate() - timedelta(days=1)
        validate_and_record(self.strategy, LedgerEntry(
            kind="DEPOSIT", occurred_on=self.quoted_day, amount=D("1000")))
        validate_and_record(self.strategy, LedgerEntry(
            kind="BUY", occurred_on=self.quoted_day, symbol="LQQ3.L",
            quantity=D("4"), unit_price=D("100")))
        self.mark = PriceMark.objects.create(
            strategy=self.strategy, symbol="LQQ3.L", price=D("110"),
            as_of=self.quoted_day, source="manual")

    def verify_yahoo_mark(self):
        self.mark.source = 'yahoo'
        self.mark.save(update_fields=['source'])
        MarketObservation.objects.create(symbol=self.mark.symbol, as_of=self.quoted_day,
            price=self.mark.price, currency='GBP')

    def run_scheduler(self):
        with patch("tracker.management.commands.sync_market_and_signals.review_strategy",
                   return_value={"status": "ok"}):
            call_command("sync_market_and_signals", verbosity=0)

    def test_manual_price_does_not_become_automated_valuation(self):
        self.run_scheduler()
        self.assertFalse(StrategyValueSnapshot.objects.filter(strategy=self.strategy).exists())

    def test_yahoo_snapshot_records_quote_day_not_scheduler_day(self):
        self.verify_yahoo_mark()
        self.run_scheduler()
        snapshot = StrategyValueSnapshot.objects.get(strategy=self.strategy)
        self.assertEqual(snapshot.as_of, self.quoted_day)
        self.assertEqual(snapshot.total_value, D("1040"))

    def test_newer_transaction_cannot_be_backvalued_with_old_price(self):
        self.verify_yahoo_mark()
        validate_and_record(self.strategy, LedgerEntry(
            kind="DEPOSIT", occurred_on=timezone.localdate(), amount=D("100")))
        self.run_scheduler()
        self.assertFalse(StrategyValueSnapshot.objects.filter(strategy=self.strategy).exists())
