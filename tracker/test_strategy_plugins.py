"""The core is strategy-agnostic: a second, test-only strategy runs end to end.

The test strategy has three funds at fixed weights and rebalances each
quarter. It is registered only inside these tests. Nothing here changes 9Sig.
"""
import re
from datetime import timedelta
from decimal import Decimal as D, ROUND_DOWN
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.core import catalogue
from tracker.core.calendar import quarter_end_before
from tracker.models import (
    MarketObservation, NotificationPreference, PriceMark, RebalanceExecution,
    StrategyInstance, StrategySignal,
)
from tracker.strategies import registry
from tracker.strategies.base import StrategyModule
from tracker.strategies.instruments import symbols

KEY = "test-fixed-weights"
VERSION = "fixed.v1.0.0"
FUNDS = (("stocks", "TSTA.L", D("0.5")), ("bonds", "TSTB.L", D("0.3")), ("gold", "TSTC.L", D("0.2")))


def _review(instance):
    """Toy rules: put each fund back to its weight when it drifts over 5 points."""
    from tracker.services import ledger_summary
    for symbol in symbols(instance):
        latest = MarketObservation.objects.filter(symbol=symbol, currency="GBP").order_by("-as_of").first()
        if latest:
            PriceMark.objects.update_or_create(
                strategy=instance, symbol=symbol,
                defaults={"price": latest.price, "as_of": latest.as_of, "source": "yahoo"})
    report = ledger_summary(instance)
    if not report["positions"] or report["value"] is None:
        return {"status": "waiting"}
    total = report["value"]
    actions = []
    for role, symbol, weight in FUNDS:
        price = report["marks"][symbol].price
        current = report["positions"].get(symbol, D("0")) * price
        gap = total * weight - current
        if abs(gap) / total > D("0.05"):
            quantity = (abs(gap) / price).quantize(D("0.01"), rounding=ROUND_DOWN)
            actions.append({"side": "BUY" if gap > 0 else "SELL", "symbol": symbol,
                            "quantity": str(quantity), "estimated_gbp": str(abs(gap).quantize(D("0.01")))})
    as_of = quarter_end_before(timezone.localdate())
    StrategySignal.objects.get_or_create(
        strategy=instance, signal_key="fixed:%s:%s" % (instance.pk, as_of),
        defaults={"kind": "QUARTERLY", "as_of": as_of, "title": "Rebalance to fixed weights",
                  "message": "Put each fund back to its target weight.", "actions": actions,
                  "strategy_version": VERSION})
    return {"status": "ok"}


PLUGIN = StrategyModule(
    key=KEY, review=_review,
    next_review=lambda day: day, last_review_before=quarter_end_before,
)
DESCRIPTION = registry.StrategyDescription(
    key=KEY, version=VERSION, title="Fixed weights (test)",
    summary="Three funds at fixed weights.", cadence="Quarterly",
    rules=("Rebalance to fixed weights each quarter.",),
    role_targets=tuple((role, weight) for role, _, weight in FUNDS),
    target_explanation="Fixed.", caution="Test only.")
OFFER = catalogue.StrategyOffer(KEY, "Fixed weights", "Moderate risk", "Quarterly",
                                "Test-only fixed-weight strategy.", True)


def _lines():
    rows = {}
    for n, (role, ticker, weight) in enumerate(FUNDS):
        line = catalogue.CatalogueLine(
            KEY, role, "GB", "TRADING212", "ISA", catalogue.CATALOGUE_VERSION,
            "Test fund %s" % role, ticker, "XS000000000%d" % n, "GBP", "GBP", "LSE GBP",
            weight, "account_check_pending")
        rows[(KEY, role, "GB", "TRADING212", "ISA", catalogue.CATALOGUE_VERSION)] = line
    return rows


class TestStrategyMixin:
    def register_test_strategy(self):
        for target, values in ((registry._RELEASED, {KEY: DESCRIPTION}),
                               (registry._EXTRA, {KEY: PLUGIN}),
                               (catalogue._OFFERS, {KEY: OFFER}),
                               (catalogue._CATALOGUE, _lines())):
            patcher = patch.dict(target, values)
            patcher.start()
            self.addCleanup(patcher.stop)


class SecondStrategyEndToEndTests(TestStrategyMixin, TestCase):
    def setUp(self):
        self.register_test_strategy()
        self.owner = get_user_model().objects.create_user("plugin-owner", password="a-long-test-password-1")
        NotificationPreference.objects.create(owner=self.owner, email_enabled=True,
                                              destination_email="owner@example.invalid",
                                              discord_enabled=False, monthly_reminders=False)
        self.client.force_login(self.owner)
        self.today = timezone.localdate()
        self.quarter = quarter_end_before(self.today)
        if self.today < self.quarter + timedelta(days=3):
            self.skipTest("Needs a few days after quarter end for fills and a later close.")
        self.close_day = self.today - timedelta(days=1)

    def close(self, prices):
        for (_, ticker, _), price in zip(FUNDS, prices):
            MarketObservation.objects.create(source="yahoo", raw_unit_verified=True, symbol=ticker, as_of=self.close_day,
                                             price=D(price), currency="GBP")

    def test_setup_review_alert_fills_and_finish(self):
        start = self.quarter - timedelta(days=20)
        # Setup page offers it, and it pins all three funds.
        self.assertContains(self.client.get(reverse("v2_add_strategy")), "Fixed weights")
        self.client.post(reverse("v2_add_strategy"), {
            "strategy": KEY, "platform": "TRADING212", "account_type": "ISA", "ack": "yes",
            "mode": "fresh", "name": "Fixed", "start": start.isoformat(),
            "amount": "1000", "deposited": "yes", "go_live": "yes"})
        strategy = StrategyInstance.objects.get(owner=self.owner)
        self.assertFalse(strategy.tracking_only)
        self.assertEqual(symbols(strategy), ("TSTA.L", "TSTB.L", "TSTC.L"))
        self.close(("100", "10", "100"))
        today_url = reverse("v2_instance_page", args=[strategy.pk, "today"])
        page = self.client.get(today_url)
        self.assertContains(page, "Buy your opening position")
        for ticker in ("TSTA.L", "TSTB.L", "TSTC.L"):
            self.assertContains(page, ticker)
        # Opening buys through the normal ledger form.
        add = reverse("v2_ledger_add", args=[strategy.pk])
        for ticker, qty, price in (("TSTA.L", "5", "100"), ("TSTB.L", "30", "10"), ("TSTC.L", "2", "100")):
            self.client.post(add, {"intent": "save", "kind": "BUY", "symbol": ticker, "quantity": qty,
                                   "unit_price": price, "fee": "0", "occurred_on": start.isoformat()})
        self.assertEqual(strategy.ledger.filter(kind="BUY").count(), 3)
        # Stocks rally: the hourly job asks the plug-in for its review.
        MarketObservation.objects.filter(symbol="TSTA.L").update(price=D("150"))
        with patch("tracker.management.commands.sync_market_and_signals.sync_ticker"):
            call_command("sync_market_and_signals", verbosity=0)
        signal = StrategySignal.objects.get(strategy=strategy)
        sides = {a["symbol"]: a["side"] for a in signal.actions}
        self.assertEqual(sides["TSTA.L"], "SELL")
        page = self.client.get(today_url)
        self.assertContains(page, "I did this")
        # The quarter-end alert goes out through the same digest as 9Sig.
        from tracker.cockpit_notifications import send_due_cockpit_notices
        with patch("tracker.services._send_message"):
            self.assertEqual(send_due_cockpit_notices(today=self.today), 1)
        # Record every suggested trade, then a close after the fills finishes it.
        review = reverse("v2_guided_review", args=[strategy.pk, signal.pk])
        fill_day = self.quarter + timedelta(days=1)
        from uuid import uuid4
        for index, action in enumerate(signal.actions):
            price = {"TSTA.L": "150", "TSTB.L": "10", "TSTC.L": "100"}[action["symbol"]]
            response = self.client.post(review, {
                "intent": "record_one", "action_index": str(index),
                "executed_on": fill_day.isoformat(), "quantity": action["quantity"],
                "unit_price": price, "fee": "0", "idempotency_key": str(uuid4()),
                "actual_confirmation": "yes"})
            self.assertEqual(response.status_code, 302, response.content[:300])
        from tracker.review_autofinish import auto_finish_reviews
        self.assertEqual(auto_finish_reviews(), 1)
        self.assertTrue(RebalanceExecution.objects.filter(signal=signal).exists())
        # Every page still renders; Study says it isn't available for this strategy.
        for section in ("today", "review", "ledger", "history", "strategy", "study"):
            self.assertEqual(self.client.get(reverse("v2_instance_page", args=[strategy.pk, section])).status_code, 200)
        self.assertContains(self.client.get(reverse("v2_instance_page", args=[strategy.pk, "study"])),
                            "isn't available for this strategy")


class GenericCodeDoesNotImportStrategyRules(SimpleTestCase):
    """CLAUDE.md boundary 4: generic views never import a strategy's rules."""

    GENERIC = (
        "v2_today.py", "v2_review.py", "v2_dashboard.py", "v2_views.py", "v2_history.py", "v2_overview.py", "history_data.py", "v2_ledger_views.py", "v2_ledger_page.py",
        "v2_ledger_core.py", "v2_onboarding.py", "v2_go_live.py", "v2_strategy.py",
        "v2_settings.py", "v2_study.py", "signal_status.py", "cockpit_execution.py",
        "cockpit_notifications.py", "review_autofinish.py", "v2_review_refresh.py",
        "market_data.py", "services.py", "management/commands/sync_market_and_signals.py",
    )
    FORBIDDEN = re.compile(
        r"^\s*(from|import)\s+[\w.]*(nine_sig|market_signals|v2_breakdown|cockpit_manual_reset)"
        r"|^\s*from\s+\.\s+import\s+study\b", re.M)

    def test_generic_modules(self):
        root = Path(settings.BASE_DIR) / "tracker"
        for name in self.GENERIC:
            source = (root / name).read_text()
            with self.subTest(module=name):
                self.assertIsNone(self.FORBIDDEN.search(source))
                self.assertNotIn("nasdaq-quarterly-target", source)
