"""A genuine 100%-up spike is not postponed to the next quarter."""
from datetime import timedelta
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from tracker.market_signals import NINE_SIG_RULE_VERSION, _alert_intraperiod_spike, quarter_end_before
from tracker.models import (
    Account, LedgerEntry, MarketObservation, StrategyBaseline,
    StrategyInstance, StrategyPreset, StrategySignal,
)


class IntraperiodSpikeTests(TestCase):
    def setUp(self):
        self.now = timezone.localdate()
        self.prev_end = quarter_end_before(self.now)
        self.cross = self.now - timedelta(days=1)
        self.owner = get_user_model().objects.create_user(
            "spike-owner", password="long-spike-test-password")
        account = Account.objects.create(
            owner=self.owner, name="Trading 212 ISA", kind="ISA",
            broker="TRADING212")
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=account,
            preset=StrategyPreset.objects.get(slug="nasdaq-quarterly-target"),
            name="9Sig current", equity_symbol="LQQ3.L",
            safe_symbol="ERNS.L", market_data_enabled=True)
        self.start = self.prev_end - timedelta(days=25)
        self.strategy.ledger.create(
            kind=LedgerEntry.DEPOSIT, occurred_on=self.start,
            amount=D("1000"))
        self.first_buy = self.strategy.ledger.create(
            kind=LedgerEntry.BUY, symbol="LQQ3.L",
            occurred_on=self.start,
            quantity=D("6"), unit_price=D("100"))
        MarketObservation.objects.create(source="yahoo", raw_unit_verified=True, 
            symbol="TQQQ", as_of=self.prev_end,
            price=D("50"), currency="USD")
        self.latest = MarketObservation.objects.create(source="yahoo", raw_unit_verified=True, 
            symbol="TQQQ", as_of=self.cross,
            price=D("101"), currency="USD")

    def trigger(self, **overrides):
        from tracker.models import NotificationPreference
        NotificationPreference.objects.update_or_create(
            owner=self.owner,
            defaults={"email_enabled": True, "discord_enabled": False})
        kwargs = dict(strategy=self.strategy, now=self.now,
                      first_buy=self.first_buy, baseline=None,
                      tqqq_latest=self.latest)
        kwargs.update(overrides)
        with patch("tracker.market_signals.dispatch") as send:
            _alert_intraperiod_spike(**kwargs)
        return send

    def test_signal_detected_within_quarter_and_dispatched_once(self):
        first = self.trigger()
        second = self.trigger()
        self.assertEqual(first.call_count, 1)
        second.assert_not_called()
        signal = StrategySignal.objects.get(strategy=self.strategy)
        self.assertEqual(signal.kind, "SPIKE_WATCH")
        self.assertEqual(signal.strategy_version, NINE_SIG_RULE_VERSION)
        self.assertEqual(signal.actions, [])
        self.assertIn("review a reset to 60%", signal.message)
        self.assertIn("preceding calendar-quarter", signal.message)
        self.assertEqual(signal.as_of, self.cross)

    def test_99_percent_gain_is_not_a_spike(self):
        self.latest.price = D("99.50")
        self.latest.save(update_fields=["price"])
        self.trigger().assert_not_called()
        self.assertFalse(StrategySignal.objects.exists())

    def test_30down_blocks_spike_reset(self):
        StrategySignal.objects.create(
            strategy=self.strategy, kind="THIRTY_DOWN",
            signal_key="30down:historic", as_of=self.prev_end,
            title="30 down", message="skip", actions=[])
        self.trigger().assert_not_called()
        self.assertFalse(StrategySignal.objects.filter(
            kind="SPIKE_WATCH").exists())

    def test_owner_rejection_of_false_30down_does_not_block_spike(self):
        StrategySignal.objects.create(
            strategy=self.strategy, kind="THIRTY_DOWN",
            signal_key="30down:wrong", as_of=self.prev_end,
            title="30 down", message="wrong", actions=[],
            review_outcome="FALSE_POSITIVE")
        self.assertEqual(self.trigger().call_count, 1)

    def test_no_spike_for_owners_who_joined_midquarter(self):
        self.first_buy.occurred_on = self.cross
        self.first_buy.save(update_fields=["occurred_on"])
        self.trigger().assert_not_called()

    def test_previously_confirmed_reset_prevents_second_spike(self):
        notice = StrategySignal.objects.create(
            strategy=self.strategy, signal_key="reset:earlier",
            kind="QUARTERLY", title="reset", as_of=self.prev_end,
            message="review a reset to 60%", actions=[])
        baseline = StrategyBaseline.objects.create(
            strategy=self.strategy, reset_signal=notice,
            as_of=self.cross, equity_value=D("600"),
            safe_value=D("400"), cash_value=D("0"),
            equity_quantity=D("6"))
        self.trigger(baseline=baseline).assert_not_called()

    def test_stale_quote_and_past_cross_cannot_invent_urgent_action(self):
        old = MarketObservation.objects.create(source="yahoo", raw_unit_verified=True, 
            symbol="TQQQ", as_of=self.prev_end - timedelta(days=5),
            price=D("120"), currency="USD")
        self.trigger(tqqq_latest=old).assert_not_called()
