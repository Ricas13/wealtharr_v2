"""End-to-end real ledger, market settlement, and signal alert behaviour."""
from datetime import date, timedelta
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from tracker.market_signals import NINE_SIG_RULE_VERSION, quarter_end_before, review_strategy
from tracker.cockpit_notifications import send_due_cockpit_notices
from tracker.models import (
    Account, LedgerEntry, MarketObservation, NotificationPreference,
    StrategyInstance, StrategyPreset, StrategySignal)
from tracker.services import validate_and_record


class RealQuarterlySignalTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            "quarter-owner", password="long-quarter-test-password")
        acc = Account.objects.create(
            owner=self.owner, name="Real 212 ISA", kind="ISA", country="GB", broker="TRADING212")
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=acc,
            preset=StrategyPreset.objects.get(slug="nasdaq-quarterly-target"),
            name="9Sig ISA", equity_symbol="LQQ3.L",
            safe_symbol="ERNS.L", market_data_enabled=True)
        # Section H routes advisory alerts only to explicitly enabled channels.
        NotificationPreference.objects.create(
            owner=self.owner, email_enabled=True, discord_enabled=False)
        self.end = quarter_end_before(timezone.localdate())
        self.begin = date(self.end.year, self.end.month-2, 1)
        self.first_trade = self.begin + timedelta(days=1)
        self.older = quarter_end_before(self.begin)
        for symbol, price, currency in [
            ("TQQQ", D("80"), "USD"), ("SPY", D("100"), "USD")]:
            MarketObservation.objects.create(
                symbol=symbol, as_of=self.older,
                price=price, currency=currency)

    def save(self, kind, day, **data):
        return validate_and_record(self.strategy, LedgerEntry(
            kind=kind, occurred_on=day, **data))

    def market(self, equity_close):
        rows = [
            ("TQQQ", D("90"), "USD"),
            ("SPY", D("98"), "USD"),
            ("LQQ3.L", equity_close, "GBP"),
            ("ERNS.L", D("100"), "GBP"),
        ]
        for symbol, price, currency in rows:
            MarketObservation.objects.create(
                symbol=symbol, as_of=self.end, price=price, currency=currency)
        # Cached latest quote is fresh even though quarterly advice is
        # calculated from the completed-quarter observation.
        fresh = {}
        for symbol, price, currency in rows:
            fresh[symbol] = MarketObservation.objects.create(
                symbol=symbol, as_of=timezone.localdate(),
                price=price, currency=currency)
        return fresh

    def test_later_same_quarter_deposit_does_not_inflate_original_equity_basis(self):
        self.save(LedgerEntry.DEPOSIT, self.begin, amount=D("1000"))
        self.save(LedgerEntry.BUY, self.first_trade,
                  symbol="LQQ3.L", quantity=D("10"), unit_price=D("60"))
        self.save(LedgerEntry.BUY, self.first_trade,
                  symbol="ERNS.L", quantity=D("4"), unit_price=D("100"))
        later = self.first_trade + timedelta(days=12)
        self.save(LedgerEntry.DEPOSIT, later, amount=D("500"))
        self.save(LedgerEntry.BUY, later, symbol="LQQ3.L",
                  quantity=D("5"), unit_price=D("60"))
        cached = self.market(D("50"))
        with patch("tracker.market_signals.sync_ticker",
                   side_effect=lambda symbol: cached[symbol]), \
             patch("tracker.market_signals.dispatch") as dispatch:
            result = review_strategy(self.strategy)
        self.assertEqual(result["status"], "ok")
        alert = StrategySignal.objects.get(
            strategy=self.strategy, kind="QUARTERLY")
        self.assertIn("£904.00", alert.message)
        self.assertEqual(alert.strategy_version, NINE_SIG_RULE_VERSION)
        self.assertTrue(any(x["side"] == "BUY" for x in alert.actions))
        # No instant duplicate quarterly alert; the reconciled instruction
        # is sent once by the scheduled digest instead.
        dispatch.assert_not_called()
        with patch('tracker.cockpit_notifications.dispatch') as digest_dispatch:
            send_due_cockpit_notices()
        self.assertEqual(digest_dispatch.call_count, 1)
        self.assertIn('quarterly-digest:', digest_dispatch.call_args.args[1])

    def test_no_action_quarter_is_saved_without_sending_a_rebalance_alert(self):
        self.save(LedgerEntry.DEPOSIT, self.begin, amount=D("1000"))
        self.save(LedgerEntry.BUY, self.first_trade,
                  symbol="LQQ3.L", quantity=D("10"), unit_price=D("60"))
        self.save(LedgerEntry.BUY, self.first_trade,
                  symbol="ERNS.L", quantity=D("4"), unit_price=D("100"))
        cached = self.market(D("65.40"))
        with patch("tracker.market_signals.sync_ticker",
                   side_effect=lambda symbol: cached[symbol]), \
             patch("tracker.market_signals.dispatch") as dispatch:
            result = review_strategy(self.strategy)
        self.assertEqual(result["status"], "ok")
        alert = StrategySignal.objects.get(
            strategy=self.strategy, kind="QUARTERLY")
        self.assertEqual(alert.actions, [])
        self.assertIsNotNone(alert.acknowledged_at)
        dispatch.assert_not_called()
