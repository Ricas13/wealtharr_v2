"""Section G: one actual fill per request; partial is never a completed reset."""
from datetime import timedelta
from decimal import Decimal as D
from uuid import uuid4
from unittest.mock import patch

from django.db import IntegrityError

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


class ReviewFixture(TestCase):
    """A current quarterly review with holdings and a verified post-fill close."""

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
        self.url = reverse("v2_guided_review", args=[self.strategy.pk, self.signal.pk])

    def fill(self, index, qty, price, *, key=None, fee="0", day=None):
        return {
            "intent": "record_one",
            "action_index": str(index),
            "executed_on": (day or self.trade_day).isoformat(),
            "quantity": str(qty), "unit_price": str(price), "fee": str(fee),
            "idempotency_key": str(key or uuid4()),
            "actual_confirmation": "yes",
        }


class ConfirmedSingleFillTests(ReviewFixture):
    def test_two_partial_sells_then_buy_finalize_actual_reset(self):
        first = self.fill(0, "0.4", "100")
        self.assertEqual(self.client.post(self.url, first).status_code, 302)
        self.assertEqual(SignalFill.objects.filter(signal=self.signal).count(), 1)
        self.assertEqual(self.strategy.ledger.filter(kind="SELL").count(), 1)
        self.assertFalse(RebalanceExecution.objects.filter(signal=self.signal).exists())
        self.assertFalse(StrategyBaseline.objects.filter(reset_signal=self.signal).exists())
        remaining_response = self.client.get(self.url)
        self.assertEqual(remaining_response.status_code, 200)
        self.assertEqual(remaining_response.context["review_rows"][0]["remaining"], D("0.6"))
        self.assertContains(self.client.get(reverse(
            "v2_instance_page", args=[self.strategy.pk, "today"])),
            "Finish recording actual fills")
        self.assertEqual(self.client.get(reverse(
            "v2_instance_page", args=[self.strategy.pk, "history"])).status_code, 200)
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

    def test_link_persistence_failure_rolls_back_real_ledger_entry(self):
        # No orphan trade is allowed if the SignalFill insert fails after
        # validate_and_record has appended the accounting event.
        original = self.strategy.ledger.count()
        with patch("tracker.cockpit_execution.SignalFill.objects.create",
                   side_effect=IntegrityError("injected link insert failure")):
            response = self.client.post(self.url, self.fill(0, "1", "100"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.strategy.ledger.count(), original)
        self.assertFalse(SignalFill.objects.filter(signal=self.signal).exists())
        self.assertFalse(RebalanceExecution.objects.filter(signal=self.signal).exists())
        self.assertFalse(StrategyBaseline.objects.filter(reset_signal=self.signal).exists())

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

    def test_top_up_after_review_still_lets_fills_be_recorded(self):
        self.client.post(self.url, self.fill(0, "1", "100"))
        validate_and_record(self.strategy, LedgerEntry(
            kind="DEPOSIT", occurred_on=self.trade_day,
            amount=D("50"), note="Actual later top-up"))
        self.client.post(self.url, self.fill(1, "2", "50"))
        self.assertEqual(SignalFill.objects.filter(signal=self.signal).count(), 2)

    def test_other_post_review_trade_or_withdrawal_invalidates_partial_completion(self):
        # A top-up doesn't change the quarter's trades; a withdrawal does.
        self.client.post(self.url, self.fill(0, "1", "100"))
        validate_and_record(self.strategy, LedgerEntry(
            kind="WITHDRAW", occurred_on=self.trade_day,
            amount=D("5"), note="Actual later withdrawal"))
        response = self.client.post(self.url, self.fill(1, "2", "50"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SignalFill.objects.filter(signal=self.signal).count(), 1)
        self.assertFalse(RebalanceExecution.objects.filter(signal=self.signal).exists())


class AutoFinishTests(ReviewFixture):
    """The hourly job finishes an ordinary review once every fill is in and a
    verified close after the last fill exists. Resets still need the owner."""

    def ordinary(self):
        StrategySignal.objects.filter(pk=self.signal.pk).update(reset_reason="")
        self.signal.refresh_from_db()

    def test_finishes_only_when_all_fills_and_a_later_close_exist(self):
        from tracker.review_autofinish import auto_finish_reviews
        self.ordinary()
        self.assertEqual(auto_finish_reviews(), 0)  # nothing recorded yet
        self.client.post(self.url, self.fill(0, "1", "100"))
        self.assertEqual(auto_finish_reviews(), 0)  # still partial
        self.client.post(self.url, self.fill(1, "2", "50"))
        self.assertEqual(auto_finish_reviews(), 1)
        record = RebalanceExecution.objects.get(signal=self.signal)
        self.assertIsNone(record.baseline_id)  # ordinary reviews never reset
        self.signal.refresh_from_db()
        self.assertEqual(self.signal.review_outcome, "CONFIRMED")
        self.assertEqual(auto_finish_reviews(), 0)  # idempotent

    def test_waits_for_a_close_after_the_last_fill(self):
        from tracker.review_autofinish import auto_finish_reviews
        self.ordinary()
        self.client.post(self.url, self.fill(0, "1", "100"))
        self.client.post(self.url, self.fill(1, "2", "50"))
        PriceMark.objects.filter(strategy=self.strategy).update(as_of=self.trade_day)
        MarketObservation.objects.filter(as_of=self.quote_day).update(as_of=self.trade_day)
        self.assertEqual(auto_finish_reviews(), 0)
        self.assertFalse(RebalanceExecution.objects.filter(signal=self.signal).exists())

    def test_reset_reviews_are_left_for_the_owner(self):
        from tracker.review_autofinish import auto_finish_reviews
        self.client.post(self.url, self.fill(0, "1", "100"))
        self.client.post(self.url, self.fill(1, "2", "50"))
        self.assertEqual(auto_finish_reviews(), 0)
        self.assertFalse(StrategyBaseline.objects.filter(reset_signal=self.signal).exists())
