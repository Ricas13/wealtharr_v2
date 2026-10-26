"""Golden Butterfly v1: pure rules, USD line conversion, and the full yearly loop."""
from datetime import date, timedelta
from decimal import Decimal as D
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from tracker.strategies.golden_butterfly.v1 import VERSION, WEIGHTS, Fund, Quote, evaluate

YEAR_END = date(2026, 12, 31)
SYMBOLS = {"stocks": "S.L", "scv": "V.L", "long": "L.L", "short": "T.L", "gold": "G.L"}
FUNDS = tuple(Fund(role, SYMBOLS[role], weight) for role, weight in WEIGHTS)


def quotes(**prices):
    base = {"S.L": "100", "V.L": "40", "L.L": "10", "T.L": "120", "G.L": "50"}
    base.update({SYMBOLS[k]: v for k, v in prices.items()})
    return {s: Quote(s, D(p), YEAR_END) for s, p in base.items()}


def on_target(total=D("10000")):
    q = quotes()
    return {f.symbol: total * f.weight / q[f.symbol].price_gbp for f in FUNDS}


class GoldenButterflyRulesTests(SimpleTestCase):
    def test_five_equal_weights(self):
        self.assertEqual(dict(WEIGHTS), {r: D("0.20") for r in ("stocks", "scv", "long", "short", "gold")})

    def test_on_target_needs_nothing_and_drift_is_rebalanced_without_borrowing(self):
        result = evaluate(funds=FUNDS, positions=on_target(), cash=D("0"), quotes=quotes(), as_of=YEAR_END)
        self.assertEqual((result.status, result.strategy_version, result.trades), ("ok", VERSION, ()))
        # Gold doubles: £2,000 -> £4,000 of £12,000; target £2,400 each.
        result = evaluate(funds=FUNDS, positions=on_target(), cash=D("0"),
                          quotes=quotes(gold="100"), as_of=YEAR_END)
        sides = {t.role: t.side for t in result.trades}
        self.assertEqual(sides["gold"], "SELL")
        self.assertEqual({r for r, s in sides.items() if s == "BUY"}, {"stocks", "scv", "long", "short"})
        sold = sum(t.estimated_gbp for t in result.trades if t.side == "SELL")
        self.assertLessEqual(sum(t.estimated_gbp for t in result.trades if t.side == "BUY"), sold)
        self.assertAlmostEqual(float(next(t for t in result.trades if t.role == "gold").estimated_gbp), 1600, delta=1)

    def test_cannot_compute_gives_zero_trades(self):
        q = quotes()
        del q["V.L"]
        result = evaluate(funds=FUNDS, positions=on_target(), cash=D("0"), quotes=q, as_of=YEAR_END)
        self.assertEqual((result.status, result.trades), ("cannot_compute", ()))
        self.assertIn("V.L", result.reason)


class UsdLineConversionTests(TestCase):
    """A USD-quoted fund is stored in pounds at that day's GBP/USD close, or not at all."""

    def chart(self, currency, closes):
        from tracker.market_data import FetchedChart
        series = sorted((day, D(v)) for day, v in closes.items())
        return FetchedChart(currency, series, currency, dict(series))

    def test_usd_close_divided_by_same_day_rate_and_missing_rate_skips_the_day(self):
        from tracker.market_data import FX_SYMBOL, sync_ticker
        from tracker.models import MarketObservation
        today = timezone.localdate()
        d1, d2, d3 = today - timedelta(days=3), today - timedelta(days=2), today - timedelta(days=1)
        charts = {"USSC.L": self.chart("USD", {d1: "80", d2: "81", d3: "82"}),
                  FX_SYMBOL: self.chart("USD", {d1: "1.25", d2: "1.20"})}
        with patch("tracker.market_data.fetch_chart", side_effect=lambda s, since: charts[s]):
            sync_ticker("USSC.L", force=True)
        rows = {o.as_of: o for o in MarketObservation.objects.filter(symbol="USSC.L")}
        self.assertEqual(set(rows), {d1, d2})
        self.assertEqual(rows[d1].price, D("64"))
        self.assertEqual(rows[d2].price, D("67.5"))
        self.assertEqual((rows[d1].currency, rows[d1].raw_close, rows[d1].raw_quote_unit),
                         ("GBP", D("80"), "USD"))
        self.assertTrue(rows[d1].raw_unit_verified)

    def test_wrong_unit_or_no_rate_stores_nothing(self):
        from tracker.market_data import FX_SYMBOL, sync_ticker
        from tracker.models import MarketObservation, YahooSync
        day = timezone.localdate() - timedelta(days=1)
        charts = {"USSC.L": self.chart("GBP", {day: "80"}), FX_SYMBOL: self.chart("USD", {day: "1.25"})}
        with patch("tracker.market_data.fetch_chart", side_effect=lambda s, since: charts[s]):
            self.assertIsNone(sync_ticker("USSC.L", force=True))
        charts = {"USSC.L": self.chart("USD", {day: "80"}), FX_SYMBOL: self.chart("USD", {day - timedelta(days=9): "1.25"})}
        with patch("tracker.market_data.fetch_chart", side_effect=lambda s, since: charts[s]):
            self.assertIsNone(sync_ticker("USSC.L", force=True))
        self.assertFalse(MarketObservation.objects.filter(symbol="USSC.L").exists())
        self.assertEqual(YahooSync.objects.get(symbol="USSC.L").last_error, "FX_UNAVAILABLE")


class GoldenButterflyEndToEndTests(TestCase):
    """Setup, go live, opening buys, a yearly review, the alert, fills and finish."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from tracker.models import NotificationPreference
        self.owner = get_user_model().objects.create_user("gb-owner", password="a-long-test-password-1")
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
                symbol=symbol, as_of=day, defaults={"source": "yahoo", "raw_unit_verified": True, "price": D(price), "currency": "GBP"})

    def latest(self, symbol, **kwargs):
        from tracker.models import MarketObservation
        return MarketObservation.objects.filter(symbol=symbol).order_by("-as_of").first()

    def test_full_year_loop(self):
        from uuid import uuid4
        from django.core.management import call_command
        from django.urls import reverse
        from tracker.models import RebalanceExecution, StrategyInstance, StrategySignal
        from tracker.review_autofinish import auto_finish_reviews
        from tracker.strategies.instruments import symbols
        start = self.year_end - timedelta(days=180)
        chosen = self.client.get(reverse("v2_add_strategy") + "?strategy=golden-butterfly")
        for isin in ("IE00BFMXXD54", "IE00BSPLC413", "IE00B6YX5L24", "IE00B4WXJK79", "IE00B4ND3602"):
            self.assertContains(chosen, isin)
        self.client.post(reverse("v2_add_strategy"), {
            "strategy": "golden-butterfly", "platform": "TRADING212", "account_type": "ISA", "ack": "yes",
            "mode": "fresh", "name": "GB", "start": start.isoformat(), "amount": "10000",
            "deposited": "yes", "go_live": "yes"})
        strategy = StrategyInstance.objects.get(owner=self.owner)
        self.assertFalse(strategy.tracking_only)
        self.assertEqual(symbols(strategy), ("VUAG.L", "USSC.L", "GLTL.L", "IGLS.L", "SGLN.L"))
        self.assertEqual(strategy.resolved_instruments["roles"]["scv"]["raw_quote_unit"], "USD")
        page = self.client.get(reverse("v2_instance_page", args=[strategy.pk, "strategy"]))
        self.assertContains(page, "Not affiliated with Portfolio Charts")
        buy = {"VUAG.L": ("20", "100"), "USSC.L": ("50", "40"), "GLTL.L": ("62.5", "32"),
               "IGLS.L": ("16", "125"), "SGLN.L": ("33.3333", "60")}
        add = reverse("v2_ledger_add", args=[strategy.pk])
        for symbol, (qty, price) in buy.items():
            self.client.post(add, {"intent": "save", "kind": "BUY", "symbol": symbol, "quantity": qty,
                                   "unit_price": price, "fee": "0", "occurred_on": start.isoformat()})
        self.assertEqual(strategy.ledger.filter(kind="BUY").count(), 5)
        # Gold rallied by year end.
        closes = {"VUAG.L": "100", "USSC.L": "40", "GLTL.L": "32", "IGLS.L": "125", "SGLN.L": "120"}
        self.observe(self.year_end, closes)
        self.observe(self.recent, closes)
        with patch("tracker.market_data.sync_ticker", side_effect=self.latest), \
                patch("tracker.management.commands.sync_market_and_signals.sync_ticker"):
            call_command("sync_market_and_signals", verbosity=0)
        signal = StrategySignal.objects.get(strategy=strategy)
        self.assertEqual((signal.as_of, signal.strategy_version), (self.year_end, VERSION))
        sides = {a["symbol"]: a["side"] for a in signal.actions}
        self.assertEqual(sides["SGLN.L"], "SELL")
        self.assertEqual(sides["USSC.L"], "BUY")
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
