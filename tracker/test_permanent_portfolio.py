"""Permanent Portfolio v1: 15-35% bands, the monthly check loop and its Study."""
from datetime import date, timedelta
from decimal import Decimal as D
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.core import catalogue
from tracker.core.calendar import month_end_before
from tracker.strategies.permanent_portfolio.v1 import VERSION, WEIGHTS, Fund, Quote, evaluate

AS_OF = date(2026, 9, 30)
SYMBOLS = {"stocks": "S.L", "long": "L.L", "cashfund": "C.L", "gold": "G.L"}
FUNDS = tuple(Fund(role, SYMBOLS[role], weight) for role, weight in WEIGHTS)


def quotes(**prices):
    base = {"S.L": "100", "L.L": "10", "C.L": "100", "G.L": "50"}
    base.update({SYMBOLS[k]: v for k, v in prices.items()})
    return {s: Quote(s, D(p), AS_OF) for s, p in base.items()}


def even(total=D("10000")):
    q = quotes()
    return {f.symbol: total * f.weight / q[f.symbol].price_gbp for f in FUNDS}


class BandRuleTests(SimpleTestCase):
    def test_drift_inside_the_band_does_nothing(self):
        # Stocks up 60%: 4,000 of 11,500 = 34.8%, still inside 35%.
        result = evaluate(funds=FUNDS, positions=even(), cash=D("0"), quotes=quotes(stocks="160"), as_of=AS_OF)
        self.assertEqual((result.status, result.reason_code, result.trades), ("ok", "WITHIN_BANDS", ()))

    def test_leaving_the_band_puts_all_four_back_to_25(self):
        # Stocks up 70%: 4,250 of 11,750 = 36.2% > 35%.
        result = evaluate(funds=FUNDS, positions=even(), cash=D("0"), quotes=quotes(stocks="170"), as_of=AS_OF)
        self.assertEqual((result.status, result.reason_code, result.strategy_version),
                         ("action_needed", "BAND_BREACH", VERSION))
        sides = {t.role: t.side for t in result.trades}
        self.assertEqual(sides["stocks"], "SELL")
        self.assertEqual({r for r, s in sides.items() if s == "BUY"}, {"long", "cashfund", "gold"})
        # A part falling below 15% also triggers: gold halves to 12.5% of 8,750 = 14.3%.
        low = evaluate(funds=FUNDS, positions=even(), cash=D("0"), quotes=quotes(gold="25"), as_of=AS_OF)
        self.assertEqual(low.reason_code, "BAND_BREACH")

    def test_a_fresh_deposit_is_invested_at_25_each_and_missing_prices_give_no_trades(self):
        opening = evaluate(funds=FUNDS, positions={}, cash=D("4000"), quotes=quotes(), as_of=AS_OF)
        self.assertEqual({t.role: t.estimated_gbp for t in opening.trades},
                         {"stocks": D("1000.00"), "long": D("1000.00"), "cashfund": D("1000.00"),
                          "gold": D("1000.00")})
        q = quotes()
        del q["G.L"]
        broken = evaluate(funds=FUNDS, positions=even(), cash=D("0"), quotes=q, as_of=AS_OF)
        self.assertEqual((broken.status, broken.trades), ("cannot_compute", ()))

    def test_offered_in_every_currency_with_its_own_funds(self):
        expect = {("TRADING212", "ISA"): ["VUAG.L", "GLTL.L", "ERNS.L", "SGLN.L"],
                  ("FIDELITY", "ROTH_IRA"): ["VTI", "TLT", "BIL", "GLD"],
                  ("TRADE_REPUBLIC", "DEPOT"): ["SXR8.DE", "DBXF.DE", "XEON.DE", "PPFB.DE"]}
        for (broker, kind), tickers in expect.items():
            with self.subTest(broker=broker):
                self.assertTrue(catalogue.selection_available("permanent-portfolio", broker, kind))
                self.assertEqual([r.ticker for r in catalogue.rows_for("permanent-portfolio", broker, kind)],
                                 tickers)


class MonthlyCheckEndToEndTests(TestCase):
    def setUp(self):
        from tracker.models import NotificationPreference
        self.owner = get_user_model().objects.create_user("pp-owner", password="a-long-test-password-1")
        NotificationPreference.objects.create(owner=self.owner, email_enabled=True,
                                              destination_email="o@example.invalid",
                                              discord_enabled=False, monthly_reminders=False)
        self.client.force_login(self.owner)
        self.today = timezone.localdate()
        self.month_end = month_end_before(self.today)
        self.recent = self.today - timedelta(days=1)
        if self.recent <= self.month_end + timedelta(days=2):
            self.skipTest("Needs a few days after the month end.")

    def observe(self, day, prices):
        from tracker.models import MarketObservation
        for symbol, price in prices.items():
            MarketObservation.objects.update_or_create(
                symbol=symbol, as_of=day, defaults={"price": D(price), "currency": "GBP"})

    def latest(self, symbol, **kwargs):
        from tracker.models import MarketObservation
        return MarketObservation.objects.filter(symbol=symbol).order_by("-as_of").first()

    def test_breach_creates_a_review_that_finishes_after_fills(self):
        from tracker.models import RebalanceExecution, StrategyInstance, StrategySignal
        from tracker.review_autofinish import auto_finish_reviews
        start = self.month_end - timedelta(days=40)
        self.client.post(reverse("v2_add_strategy"), {
            "strategy": "permanent-portfolio", "platform": "TRADING212", "account_type": "ISA", "ack": "yes",
            "mode": "fresh", "name": "PP", "start": start.isoformat(), "amount": "4000",
            "deposited": "yes", "go_live": "yes"})
        strategy = StrategyInstance.objects.get(owner=self.owner)
        self.assertFalse(strategy.tracking_only)
        page = self.client.get(reverse("v2_instance_page", args=[strategy.pk, "today"]))
        self.assertEqual(len(page.context["today_opening"]), 4)
        for symbol, qty, price in (("VUAG.L", "10", "100"), ("GLTL.L", "31.25", "32"),
                                   ("ERNS.L", "10", "100"), ("SGLN.L", "16.6666", "60")):
            self.client.post(reverse("v2_ledger_add", args=[strategy.pk]), {
                "intent": "save", "kind": "BUY", "symbol": symbol, "quantity": qty, "unit_price": price,
                "fee": "0", "occurred_on": start.isoformat()})
        # Gold doubled: 2,000 of 5,000 = 40%, outside the band.
        closes = {"VUAG.L": "100", "GLTL.L": "32", "ERNS.L": "100", "SGLN.L": "120"}
        self.observe(self.month_end, closes)
        self.observe(self.recent, closes)
        with patch("tracker.market_data.sync_ticker", side_effect=self.latest), \
                patch("tracker.management.commands.sync_market_and_signals.sync_ticker"):
            call_command("sync_market_and_signals", verbosity=0)
        signal = StrategySignal.objects.get(strategy=strategy)
        self.assertEqual((signal.as_of, signal.strategy_version), (self.month_end, VERSION))
        self.assertIn("outside 15-35%", signal.message)
        self.assertEqual({a["symbol"]: a["side"] for a in signal.actions}["SGLN.L"], "SELL")
        review = reverse("v2_guided_review", args=[strategy.pk, signal.pk])
        for index, action in enumerate(signal.actions):
            response = self.client.post(review, {
                "intent": "record_one", "action_index": str(index),
                "executed_on": (self.month_end + timedelta(days=2)).isoformat(),
                "quantity": action["quantity"], "unit_price": closes[action["symbol"]], "fee": "0",
                "idempotency_key": str(uuid4()), "actual_confirmation": "yes"})
            self.assertEqual(response.status_code, 302, response.context and response.context.get("review_error"))
        self.assertEqual(auto_finish_reviews(), 1)
        self.assertTrue(RebalanceExecution.objects.filter(signal=signal).exists())
        study = self.client.get(reverse("v2_instance_page", args=[strategy.pk, "study"]))
        self.assertContains(study, "same Permanent Portfolio rules")


class PermanentStudyTests(TestCase):
    def test_monthly_band_replay_in_dollars(self):
        from tracker.strategies.registry import module_for_key
        from tracker.test_study import TODAY, seed
        cache.clear()
        start = TODAY - timedelta(days=800)
        seed("SPY", start, lambda d, i: 400.0)
        seed("VTI", start, lambda d, i: 200 * (1.002 ** i))  # shares run away from the others
        seed("TLT", start, lambda d, i: 90.0)
        seed("BIL", start, lambda d, i: 91.0)
        seed("GLD", start, lambda d, i: 250.0)
        study = module_for_key("permanent-portfolio").study["USD"]
        data = study.load_data()
        result = study.run(data, "1y", 1000, 0)
        # Opening 4 buys plus at least one band rebalance.
        self.assertGreater(result["series"]["strategy"]["trades"], 4)
        self.assertIn("US dollars", " ".join(study.how_it_works(data)))
        self.assertIn("only when one is below 15% or above 35%", " ".join(study.how_it_works(data)))
