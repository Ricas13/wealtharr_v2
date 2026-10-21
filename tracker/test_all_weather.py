"""All Weather (UK) v1: pure yearly-rebalance rules."""
from datetime import date, timedelta
from decimal import Decimal as D

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from tracker.strategies.all_weather.v1 import Fund, Quote, WEIGHTS, evaluate

YEAR_END = date(2026, 12, 31)
SYMBOLS = {"stocks": "S.L", "long": "L.L", "inter": "I.L", "gold": "G.L", "comm": "C.L"}
FUNDS = tuple(Fund(role, SYMBOLS[role], weight) for role, weight in WEIGHTS)


def quotes(**prices):
    base = {"S.L": "100", "L.L": "10", "I.L": "10", "G.L": "50", "C.L": "5"}
    base.update({SYMBOLS[k]: v for k, v in prices.items()})
    return {s: Quote(s, D(p), YEAR_END) for s, p in base.items()}


def on_target(total=D("10000")):
    q = quotes()
    return {f.symbol: total * f.weight / q[f.symbol].price_gbp for f in FUNDS}


class AllWeatherRulesTests(SimpleTestCase):
    def test_weights_are_the_published_mix(self):
        self.assertEqual(dict(WEIGHTS), {"stocks": D("0.30"), "long": D("0.40"), "inter": D("0.15"),
                                         "gold": D("0.075"), "comm": D("0.075")})
        self.assertEqual(sum(w for _, w in WEIGHTS), D("1"))

    def test_on_target_needs_no_trades(self):
        result = evaluate(funds=FUNDS, positions=on_target(), cash=D("0"), quotes=quotes(), as_of=YEAR_END)
        self.assertEqual((result.status, result.trades), ("ok", ()))

    def test_rally_sells_the_winner_and_buys_the_rest_without_borrowing(self):
        # Shares double: £3,000 -> £6,000 of a £13,000 portfolio.
        result = evaluate(funds=FUNDS, positions=on_target(), cash=D("0"),
                          quotes=quotes(stocks="200"), as_of=YEAR_END)
        self.assertEqual(result.status, "action_needed")
        sides = {t.symbol: t.side for t in result.trades}
        self.assertEqual(sides["S.L"], "SELL")
        self.assertTrue(all(sides[s] == "BUY" for s in ("L.L", "I.L", "G.L", "C.L")))
        sold = sum(t.estimated_gbp for t in result.trades if t.side == "SELL")
        bought = sum(t.estimated_gbp for t in result.trades if t.side == "BUY")
        self.assertLessEqual(bought, sold)
        # Sells first, then buys; every action carries the price and date it used.
        self.assertEqual([t.side for t in result.trades][0], "SELL")
        action = result.trades[0].as_action()
        self.assertEqual((action["price_date"], action["role"]), ("2026-12-31", "stocks"))
        # After trading, shares are back to ~30% of the total.
        stocks_after = (on_target()["S.L"] - result.trades[0].quantity) * D("200")
        self.assertAlmostEqual(float(stocks_after / D("13000")), 0.30, places=3)

    def test_new_cash_is_split_at_the_weights(self):
        result = evaluate(funds=FUNDS, positions=on_target(), cash=D("2000"), quotes=quotes(), as_of=YEAR_END)
        buys = {t.role: t.estimated_gbp for t in result.trades}
        self.assertTrue(all(t.side == "BUY" for t in result.trades))
        self.assertAlmostEqual(float(buys["long"]), 800, delta=1)
        self.assertAlmostEqual(float(buys["gold"]), 150, delta=1)

    def test_small_drifts_are_ignored(self):
        positions = on_target()
        positions["S.L"] += D("0.1")  # £10 over
        result = evaluate(funds=FUNDS, positions=positions, cash=D("0"), quotes=quotes(), as_of=YEAR_END)
        self.assertEqual(result.trades, ())

    def test_cannot_compute_gives_zero_trades_and_one_reason(self):
        q = quotes()
        del q["G.L"]
        cases = (
            dict(positions=on_target(), cash=D("0"), quotes=q),
            dict(positions={**on_target(), "OTHER.L": D("1")}, cash=D("0"), quotes=quotes()),
            dict(positions=on_target(), cash=D("-1"), quotes=quotes()),
            dict(positions={}, cash=D("0"), quotes=quotes()),
        )
        for case in cases:
            with self.subTest(case=case):
                result = evaluate(funds=FUNDS, as_of=YEAR_END, **case)
                self.assertEqual((result.status, result.trades), ("cannot_compute", ()))
                self.assertTrue(result.reason)

    def test_never_sells_more_than_held(self):
        positions = on_target()
        result = evaluate(funds=FUNDS, positions=positions, cash=D("0"),
                          quotes=quotes(stocks="1000"), as_of=YEAR_END)
        sell = next(t for t in result.trades if t.side == "SELL")
        self.assertLessEqual(sell.quantity, positions["S.L"])


class AllWeatherEndToEndTests(TestCase):
    """Setup, go live, opening buys, a yearly review, the alert, fills and finish."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from tracker.models import NotificationPreference
        self.owner = get_user_model().objects.create_user("aw-owner", password="a-long-test-password-1")
        NotificationPreference.objects.create(owner=self.owner, email_enabled=True,
                                              destination_email="o@example.invalid",
                                              discord_enabled=False, monthly_reminders=False)
        self.client.force_login(self.owner)
        self.today = timezone.localdate()
        self.year_end = date(self.today.year - 1, 12, 31)
        self.recent = self.today - timedelta(days=1)
        if self.recent <= self.year_end + timedelta(days=2):
            self.skipTest("Needs a few days after the year end for fills and a later close.")

    def observe(self, day, prices):
        from tracker.models import MarketObservation
        for symbol, price in prices.items():
            MarketObservation.objects.update_or_create(
                symbol=symbol, as_of=day, defaults={"price": D(price), "currency": "GBP"})

    def latest(self, symbol, **kwargs):
        from tracker.models import MarketObservation
        return MarketObservation.objects.filter(symbol=symbol).order_by("-as_of").first()

    def test_full_year_loop(self):
        from unittest.mock import patch
        from uuid import uuid4
        from django.core.management import call_command
        from django.urls import reverse
        from tracker.models import RebalanceExecution, StrategyInstance, StrategySignal
        from tracker.review_autofinish import auto_finish_reviews
        from tracker.strategies.instruments import symbols
        start = self.year_end - timedelta(days=180)
        self.assertContains(self.client.get(reverse("v2_add_strategy")), "All Weather (UK)")
        chosen = self.client.get(reverse("v2_add_strategy") + "?strategy=all-weather")
        for isin in ("IE00BFMXXD54", "IE00B6YX5L24", "IE00B1FZSB30", "IE00B4ND3602", "IE00BD6FTQ80"):
            self.assertContains(chosen, isin)
        self.assertNotContains(chosen, "IE00BLRPRL42")
        self.client.post(reverse("v2_add_strategy"), {
            "strategy": "all-weather", "platform": "TRADING212", "account_type": "ISA", "ack": "yes",
            "mode": "fresh", "name": "AW", "start": start.isoformat(), "amount": "10000",
            "deposited": "yes", "go_live": "yes"})
        strategy = StrategyInstance.objects.get(owner=self.owner)
        self.assertFalse(strategy.tracking_only)
        self.assertEqual(symbols(strategy), ("VUAG.L", "GLTL.L", "IGLT.L", "SGLN.L", "CMOP.L"))
        page = self.client.get(reverse("v2_instance_page", args=[strategy.pk, "strategy"]))
        self.assertContains(page, "not affiliated with Bridgewater")
        # Opening buys at the weights (prices in pounds).
        buy = {"VUAG.L": ("30", "100"), "GLTL.L": ("125", "32"), "IGLT.L": ("150", "10"),
               "SGLN.L": ("12.5", "60"), "CMOP.L": ("30", "25")}
        add = reverse("v2_ledger_add", args=[strategy.pk])
        for symbol, (qty, price) in buy.items():
            self.client.post(add, {"intent": "save", "kind": "BUY", "symbol": symbol, "quantity": qty,
                                   "unit_price": price, "fee": "0", "occurred_on": start.isoformat()})
        self.assertEqual(strategy.ledger.filter(kind="BUY").count(), 5)
        # US shares rallied by year end.
        closes = {"VUAG.L": "150", "GLTL.L": "32", "IGLT.L": "10", "SGLN.L": "60", "CMOP.L": "25"}
        self.observe(self.year_end, closes)
        self.observe(self.recent, closes)
        with patch("tracker.market_data.sync_ticker", side_effect=self.latest), \
                patch("tracker.management.commands.sync_market_and_signals.sync_ticker"):
            call_command("sync_market_and_signals", verbosity=0)
        signal = StrategySignal.objects.get(strategy=strategy)
        self.assertEqual(signal.as_of, self.year_end)
        sides = {a["symbol"]: a["side"] for a in signal.actions}
        self.assertEqual(sides["VUAG.L"], "SELL")
        self.assertEqual(sides["GLTL.L"], "BUY")
        self.assertContains(self.client.get(reverse("v2_instance_page", args=[strategy.pk, "today"])), "I did this")
        from tracker.cockpit_notifications import send_due_cockpit_notices
        with patch("tracker.services._send_message"):
            self.assertEqual(send_due_cockpit_notices(today=self.today), 1)
        review = reverse("v2_guided_review", args=[strategy.pk, signal.pk])
        for index, action in enumerate(signal.actions):
            response = self.client.post(review, {
                "intent": "record_one", "action_index": str(index),
                "executed_on": (self.year_end + timedelta(days=2)).isoformat(),
                "quantity": action["quantity"], "unit_price": closes[action["symbol"]], "fee": "0",
                "idempotency_key": str(uuid4()), "actual_confirmation": "yes"})
            self.assertEqual(response.status_code, 302,
                             response.context and response.context.get("review_error"))
        self.assertEqual(auto_finish_reviews(), 1)
        self.assertTrue(RebalanceExecution.objects.filter(signal=signal).exists())
        # One review per year: running again adds nothing.
        with patch("tracker.market_data.sync_ticker", side_effect=self.latest), \
                patch("tracker.management.commands.sync_market_and_signals.sync_ticker"):
            call_command("sync_market_and_signals", verbosity=0)
        self.assertEqual(StrategySignal.objects.filter(strategy=strategy).count(), 1)
