"""Section G: one actual fill per request; partial is never a completed reset."""
from datetime import timedelta
from decimal import Decimal as D
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.market_signals import quarter_end_before
from tracker.models import (
    Account, LedgerEntry, MarketObservation, PriceMark, RebalanceExecution,
    SignalFill, StrategyBaseline, StrategyInstance, StrategyPreset, StrategySignal,
)
from tracker.services import ledger_summary, validate_and_record


class ConfirmedSingleFillTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            "manual_fill_owner", password="long-test-password")
        self.other = get_user_model().objects.create_user(
            "manual_fill_other", password="another-test-password")
        self.client.force_login(self.owner)
        self.now = timezone.localdate()
        self.quarter = quarter_end_before(self.now)
        self.trade_day = self.quarter + timedelta(days=1)
        if self.trade_day >= self.now:
            self.skipTest("No post-quarter broker fill date is yet available.")
        self.quote_day = self.now - timedelta(days=1)
        if self.quote_day <= self.trade_day:
            self.skipTest("No completed post-fill market date available in this calendar window.")
        account = Account.objects.create(
            owner=self.owner, name="Manual Trading 212 ISA", kind="ISA",
            country="GB", broker="TRADING212")
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "9Sig", "summary": "", "risk": "HIGH",
                      "active": True})
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=account, preset=preset,
            name="9Sig manual", equity_symbol="LQQ3.L",
            safe_symbol="ERNS.L", market_data_enabled=True)
        funding_day = self.quarter - timedelta(days=10)
        for data in (
            dict(kind="DEPOSIT", amount=D("1000"), occurred_on=funding_day),
            dict(kind="BUY", symbol="LQQ3.L", quantity=D("7"),
                 unit_price=D("100"), occurred_on=funding_day),
            dict(kind="BUY", symbol="ERNS.L", quantity=D("6"),
                 unit_price=D("50"), occurred_on=funding_day),
        ):
            validate_and_record(self.strategy, LedgerEntry(**data))
        for symbol, price in (("LQQ3.L", "100"), ("ERNS.L", "50")):
            MarketObservation.objects.update_or_create(
                symbol=symbol, as_of=self.quote_day,
                defaults={"price": D(price), "currency": "GBP"})
            PriceMark.objects.create(
                strategy=self.strategy, symbol=symbol,
                as_of=self.quote_day, price=D(price), source="yahoo")
        self.signal = StrategySignal.objects.create(
            strategy=self.strategy, kind="QUARTERLY",
            as_of=self.quarter, signal_key="quarter:g:%s" % self.quarter,
            title="Reviewed source reset", message="Owner should verify the portfolio",
            reset_reason="eight-quarter reset approved by released strategy",
            actions=[
                {"side": "SELL", "symbol": "LQQ3.L", "quantity": "1",
                 "estimated_gbp": "100"},
                {"side": "BUY", "symbol": "ERNS.L", "quantity": "2",
                 "estimated_gbp": "100"},
            ])
        self.url = reverse("cockpit_rebalance", args=[self.signal.pk])

    def fill(self, index, qty, price, *, key=None, fee="0", day=None):
        return {
            "intent": "record_one",
            "action_index": str(index),
            "executed_on": (day or self.trade_day).isoformat(),
            "quantity": str(qty), "unit_price": str(price), "fee": str(fee),
            "idempotency_key": str(key or uuid4()),
        }

    def test_two_partial_sells_then_buy_finalize_actual_reset(self):
        first = self.fill(0, "0.4", "100")
        self.assertEqual(self.client.post(self.url, first).status_code, 302)
        self.assertEqual(SignalFill.objects.filter(signal=self.signal).count(), 1)
        self.assertEqual(self.strategy.ledger.filter(kind="SELL").count(), 1)
        self.assertFalse(RebalanceExecution.objects.filter(signal=self.signal).exists())
        self.assertFalse(StrategyBaseline.objects.filter(reset_signal=self.signal).exists())
        self.assertContains(self.client.get(self.url), "0.6 still to record")
        self.assertContains(self.client.get(reverse("cockpit_today")),
                            "Continue your recorded partial fills")
        self.assertEqual(self.client.post(self.url, {"intent": "finalize"}).status_code, 200)
        self.assertFalse(RebalanceExecution.objects.filter(signal=self.signal).exists())
        self.assertEqual(self.client.post(self.url, first).status_code, 302)
        self.assertEqual(SignalFill.objects.filter(signal=self.signal).count(), 1)
        self.assertEqual(self.client.post(self.url, self.fill(0, "0.6", "100")).status_code, 302)
        self.assertEqual(self.client.post(self.url, self.fill(1, "2", "50")).status_code, 302)
        self.assertEqual(SignalFill.objects.filter(signal=self.signal).count(), 3)
        self.assertFalse(StrategyBaseline.objects.filter(reset_signal=self.signal).exists())
        response = self.client.post(self.url, {"intent": "finalize"})
        self.assertEqual(response.status_code, 302)
        record = RebalanceExecution.objects.get(signal=self.signal)
        self.assertEqual(record.executed_on, self.trade_day)
        self.assertIsNotNone(record.baseline_id)
        baseline = StrategyBaseline.objects.get(pk=record.baseline_id)
        self.assertEqual(baseline.equity_quantity, D("6"))
        self.assertEqual(baseline.equity_value, D("600"))
        self.assertEqual(baseline.safe_value, D("400"))
        self.assertEqual(baseline.cash_value, D("0"))
        self.assertEqual(baseline.as_of, self.quote_day)
        self.assertTrue(baseline.quote_evidence["growth"]["observation_id"])
        self.assertTrue(baseline.quote_evidence["safe"]["observation_id"])
        self.assertEqual(baseline.quote_evidence["growth"]["as_of"],
                         self.quote_day.isoformat())
        self.signal.refresh_from_db()
        self.assertEqual(self.signal.review_outcome, "CONFIRMED")
        again = self.client.post(self.url, {"intent": "finalize"})
        self.assertEqual(again.status_code, 302)
        self.assertEqual(StrategyBaseline.objects.filter(reset_signal=self.signal).count(), 1)

    def test_price_not_after_fill_keeps_three_actual_entries_but_no_baseline(self):
        self.client.post(self.url, self.fill(0, "1", "100"))
        self.client.post(self.url, self.fill(1, "2", "50"))
        for mark in PriceMark.objects.filter(strategy=self.strategy):
            mark.as_of = self.trade_day
            mark.save(update_fields=["as_of"])
            MarketObservation.objects.update_or_create(
                symbol=mark.symbol, as_of=self.trade_day,
                defaults={"price": mark.price, "currency": "GBP"})
        self.assertEqual(self.client.post(self.url, {"intent": "finalize"}).status_code, 200)
        self.assertEqual(SignalFill.objects.filter(signal=self.signal).count(), 2)
        self.assertFalse(RebalanceExecution.objects.filter(signal=self.signal).exists())
        self.assertFalse(StrategyBaseline.objects.filter(reset_signal=self.signal).exists())

    def test_one_fill_over_proposed_units_is_refused_atomically(self):
        before = self.strategy.ledger.count()
        response = self.client.post(self.url, self.fill(0, "1.1", "100"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.strategy.ledger.count(), before)
        self.assertFalse(SignalFill.objects.exists())

    def test_changing_idempotency_payload_refused(self):
        key = uuid4()
        self.client.post(self.url, self.fill(0, "0.5", "100", key=key))
        self.client.post(self.url, self.fill(0, "0.6", "100", key=key))
        self.assertEqual(self.strategy.ledger.filter(kind="SELL").count(), 1)
        self.assertEqual(SignalFill.objects.filter(signal=self.signal).count(), 1)

    def test_no_automated_baseline_without_explicit_structured_reset(self):
        self.signal.reset_reason = ""
        self.signal.message = "This is text: review a reset to 60%"
        self.signal.save(update_fields=["reset_reason", "message"])
        self.client.post(self.url, self.fill(0, "1", "100"))
        self.client.post(self.url, self.fill(1, "2", "50"))
        self.client.post(self.url, {"intent": "finalize"})
        completed = RebalanceExecution.objects.get(signal=self.signal)
        self.assertIsNone(completed.baseline)
        self.assertFalse(StrategyBaseline.objects.filter(reset_signal=self.signal).exists())

    def test_other_owner_cannot_record_an_execution(self):
        self.client.force_login(self.other)
        before = self.strategy.ledger.count()
        self.assertEqual(self.client.post(self.url, self.fill(0, "1", "100")).status_code, 404)
        self.assertEqual(self.strategy.ledger.count(), before)

    def test_any_other_post_review_ledger_event_invalidates_partial_completion(self):
        self.client.post(self.url, self.fill(0, "1", "100"))
        validate_and_record(self.strategy, LedgerEntry(
            kind="DEPOSIT", occurred_on=self.trade_day,
            amount=D("50"), note="Actual later top-up"))
        response = self.client.post(self.url, self.fill(1, "2", "50"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SignalFill.objects.filter(signal=self.signal).count(), 1)
        self.assertFalse(RebalanceExecution.objects.filter(signal=self.signal).exists())
