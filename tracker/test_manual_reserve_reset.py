"""The owner may ELECT a 60/40 reset after an empty reserve; never an auto-reset.

The existing single-fill and next-day GBP baseline confirmation remain the
only executable bookkeeping pathway. Prices and owners are always checked.
"""
from datetime import timedelta
from decimal import Decimal as D
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.cockpit_manual_reset import choose_manual_reserve_reset
from tracker.market_signals import NINE_SIG_RULE_VERSION, quarter_end_before
from tracker.models import (
    Account, LedgerEntry, MarketObservation, PriceMark, RebalanceExecution,
    StrategyBaseline, StrategyInstance, StrategyPreset, StrategySignal,
)
from tracker.services import validate_and_record


class ManualReserveResetChoiceTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            "reserve_reset_owner", password="long-test-password")
        self.other = get_user_model().objects.create_user(
            "reserve_reset_other", password="another-test-password")
        self.client.force_login(self.owner)
        today = timezone.localdate()
        self.quarter = quarter_end_before(today)
        self.trade_day = self.quarter + timedelta(days=1)
        self.quote_day = today - timedelta(days=1)
        if self.quote_day <= self.trade_day:
            self.skipTest("A later verified close is required for actual fills.")
        account = Account.objects.create(
            owner=self.owner, name="Trading 212 9Sig ISA", kind="ISA",
            country="GB", broker="TRADING212")
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults=dict(name="9Sig", summary="", risk="HIGH", active=True))
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=account, preset=preset,
            name="9Sig owner", equity_symbol="LQQ3.L",
            safe_symbol="ERNS.L", market_data_enabled=True)
        funded_on = self.quarter - timedelta(days=10)
        for data in (
            dict(kind="DEPOSIT", amount=D("1050"), occurred_on=funded_on),
            dict(kind="BUY", symbol="LQQ3.L", quantity=D("10"),
                 unit_price=D("100"), occurred_on=funded_on),
            dict(kind="BUY", symbol="ERNS.L", quantity=D("1"),
                 unit_price=D("50"), occurred_on=funded_on),
        ):
            validate_and_record(self.strategy, LedgerEntry(**data))
        for symbol, price in (("LQQ3.L", "100"), ("ERNS.L", "50")):
            for when in (self.quarter, self.quote_day):
                MarketObservation.objects.update_or_create(
                    symbol=symbol, as_of=when,
                    defaults=dict(price=D(price), currency="GBP"))
            PriceMark.objects.create(
                strategy=self.strategy, symbol=symbol, as_of=self.quote_day,
                price=D(price), source="yahoo")
        self.original = StrategySignal.objects.create(
            strategy=self.strategy, signal_key="quarter:reserve-v2",
            kind="QUARTERLY", title="v2 exhausted reserve",
            as_of=self.quarter, strategy_version=NINE_SIG_RULE_VERSION,
            message=("Equity below target. Your reserve is now empty. "
                     "Review whether to reset to 60/40."),
            actions=[
                dict(side="SELL", symbol="ERNS.L",
                     quantity="1", estimated_gbp="50.00"),
                dict(side="BUY", symbol="LQQ3.L",
                     quantity="0.5", estimated_gbp="50.00"),
            ],
        )
        self.url = reverse("cockpit_rebalance", args=[self.original.pk])

    def request_reset(self):
        response = self.client.post(
            self.url, {"intent": "request_manual_reset"})
        return response

    def test_owner_option_creates_reset_alternative_without_orders_or_baseline(self):
        view = self.client.get(self.url)
        self.assertContains(view, "Choose manual 60/40 reset review")
        before = self.strategy.ledger.count()
        response = self.request_reset()
        self.assertEqual(response.status_code, 302)
        self.original.refresh_from_db()
        self.assertEqual(self.original.review_outcome, "NOT_APPLICABLE")
        # Preserves the original v2 strategy advice for audit.
        self.assertEqual(self.original.actions[0]["symbol"], "ERNS.L")
        reset = StrategySignal.objects.get(
            strategy=self.strategy, signal_key="owner-reset:quarter:reserve-v2")
        self.assertEqual(reset.strategy_version, NINE_SIG_RULE_VERSION)
        self.assertIn("Owner-elected", reset.reset_reason)
        self.assertEqual(
            [(a["side"], a["symbol"], D(a["quantity"])) for a in reset.actions],
            [("SELL", "LQQ3.L", D("3.7000")),
             ("BUY", "ERNS.L", D("7.4000"))])
        self.assertEqual(self.strategy.ledger.count(), before)
        self.assertFalse(RebalanceExecution.objects.exists())
        self.assertFalse(StrategyBaseline.objects.exists())
        # Idempotent helper: no second reset signal or unobserved ledger event.
        again = choose_manual_reserve_reset(
            owner=self.owner, strategy_id=self.strategy.pk,
            signal_id=self.original.pk)
        self.assertEqual(again.pk, reset.pk)
        self.assertEqual(StrategySignal.objects.count(), 2)

    def test_actual_single_fills_and_later_quotes_required_to_finish(self):
        self.request_reset()
        reset = StrategySignal.objects.get(
            strategy=self.strategy, signal_key="owner-reset:quarter:reserve-v2")
        url = reverse("cockpit_rebalance", args=[reset.pk])
        self.assertEqual(self.client.post(url, {"intent": "finalize"}).status_code, 200)
        self.assertFalse(StrategyBaseline.objects.exists())
        for index, qty, price in ((0, "3.7", "100"), (1, "7.4", "50")):
            response = self.client.post(url, dict(
                intent="record_one", action_index=str(index),
                executed_on=self.trade_day.isoformat(),
                quantity=qty, unit_price=price, fee="0",
                idempotency_key=str(uuid4()),
            ))
            self.assertEqual(response.status_code, 302)
        self.assertFalse(StrategyBaseline.objects.exists())
        self.assertEqual(self.client.post(url, {"intent": "finalize"}).status_code, 302)
        baseline = StrategyBaseline.objects.get(reset_signal=reset)
        self.assertEqual(baseline.equity_value, D("630.00"))
        self.assertEqual(baseline.safe_value, D("420.00"))
        self.assertEqual(baseline.cash_value, D("0.00"))
        self.assertEqual(baseline.as_of, self.quote_day)
        self.assertTrue(baseline.quote_evidence["growth"]["observation_id"])
        self.assertEqual(RebalanceExecution.objects.filter(signal=reset).count(), 1)

    def test_other_owner_and_intervening_activity_cannot_choose_reset(self):
        self.client.force_login(self.other)
        self.assertEqual(self.request_reset().status_code, 404)
        self.client.force_login(self.owner)
        validate_and_record(self.strategy, LedgerEntry(
            kind="DEPOSIT", amount=D("10"), occurred_on=self.trade_day))
        self.assertEqual(self.request_reset().status_code, 200)
        self.assertFalse(StrategySignal.objects.filter(
            signal_key__startswith="owner-reset:").exists())

    def test_unverified_quarter_close_refuses_without_mutating_original(self):
        MarketObservation.objects.filter(
            symbol="ERNS.L", as_of=self.quarter).delete()
        self.assertEqual(self.request_reset().status_code, 200)
        self.original.refresh_from_db()
        self.assertEqual(self.original.review_outcome, "")
        self.assertEqual(StrategySignal.objects.count(), 1)

    def test_already_started_regular_all_in_cannot_turn_into_manual_reset(self):
        response = self.client.post(self.url, dict(
            intent="record_one", action_index="0",
            executed_on=self.trade_day.isoformat(),
            quantity="1", unit_price="50", fee="0",
            idempotency_key=str(uuid4()),
        ))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.request_reset().status_code, 302)
        self.assertFalse(StrategySignal.objects.filter(
            signal_key__startswith="owner-reset:").exists())
