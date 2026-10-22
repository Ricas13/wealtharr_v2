"""HFEA (55/45 3x stocks/3x Treasuries, quarterly) and 3Sig (3% quarterly signal, 80/20)."""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.core import catalogue
from tracker.market_signals import count_skipped_equity_sells, quarter_end_before, quarter_number, review_strategy
from tracker.models import LedgerEntry, MarketObservation, StrategyInstance, StrategySignal
from tracker.services import validate_and_record
from tracker.strategies.hfea import v1 as hfea
from tracker.strategies.registry import module_for_key
from tracker.strategies.three_sig import v1 as three
from tracker.test_study import TODAY, seed


def _quarter_close(q):
    y, index = divmod(q, 4)
    month = (index + 1) * 3
    return date(y, month, monthrange(y, month)[1])


class HfeaRuleTests(SimpleTestCase):
    def test_weights_and_quarterly_schedule(self):
        self.assertEqual(dict(hfea.WEIGHTS), {"stocks3x": D("0.55"), "bonds3x": D("0.45")})
        plugin = module_for_key("hfea")
        self.assertEqual(plugin.cadence_word, "quarterly")
        self.assertEqual(plugin.next_review(date(2026, 4, 2)), date(2026, 6, 30))
        self.assertEqual(plugin.last_review_before(date(2026, 4, 2)), date(2026, 3, 31))

    def test_drift_goes_back_to_55_45(self):
        funds = tuple(hfea.Fund(r, r, w) for r, w in hfea.WEIGHTS)
        quotes = {r: hfea.Quote(r, D("10"), date(2026, 3, 31)) for r, _ in hfea.WEIGHTS}
        result = hfea.evaluate(funds=funds, positions={"stocks3x": D("70"), "bonds3x": D("30")},
                               cash=D("0"), quotes=quotes, as_of=date(2026, 3, 31))
        sides = {t.role: t.side for t in result.trades}
        self.assertEqual(sides, {"stocks3x": "SELL", "bonds3x": "BUY"})

    def test_catalogue_offers_trading212_and_fidelity_not_trade_republic(self):
        rows = {r.role: r.ticker for r in catalogue.rows_for("hfea", "TRADING212", "ISA")}
        self.assertEqual(rows, {"stocks3x": "3LUS.L", "bonds3x": "3TYL.L"})
        self.assertTrue(catalogue.selection_available("hfea", "TRADING212", "ISA"))
        rows = {r.role: r.ticker for r in catalogue.rows_for("hfea", "FIDELITY", "BROKERAGE")}
        self.assertEqual(rows, {"stocks3x": "UPRO", "bonds3x": "TMF"})
        self.assertFalse(catalogue.selection_available("hfea", "TRADE_REPUBLIC", "DEPOT"))
        units = catalogue.expected_quote_units()
        self.assertEqual(units["3LUS.L"], ("GBp", "GBX"))
        self.assertEqual(units["3TYL.L"], ("GBp", "GBX"))
        self.assertEqual(units["ISP6.L"], ("GBp", "GBX"))

    def test_difference_from_the_original_is_on_the_page(self):
        from tracker.strategies.registry import describe_strategy
        text = " ".join(describe_strategy("hfea").rules) + describe_strategy("hfea").caution
        self.assertIn("10-year", text)
        self.assertIn("TMF", text)


class HfeaStudyTests(TestCase):
    def test_before_launch_the_fund_is_rebuilt_at_3x_and_flagged(self):
        cache.clear()
        start = TODAY - timedelta(days=800)
        seed("GBPUSD=X", start, lambda d, i: 1.25)
        seed("SPY", start, lambda d, i: 400 * (1.001 ** i))
        seed("VTI", start, lambda d, i: 200.0)
        seed("IEF", start, lambda d, i: 100.0)
        launch = TODAY - timedelta(days=200)
        seed("3LUS.L", launch, lambda d, i: 50.0, currency="GBP")
        seed("3TYL.L", launch, lambda d, i: 20.0, currency="GBP")
        study = module_for_key("hfea").study["GBP"]
        data = study.load_data()
        self.assertTrue(data.ready)
        k = data.days.index(next(d for d in data.days if d >= launch))
        # The rebuilt line moves about three times SPY's daily rise before launch.
        rise = data.prices["stocks3x"][k - 1] / data.prices["stocks3x"][k - 2] - 1
        self.assertAlmostEqual(rise, 0.003 - 0.0075 / 252, places=6)
        # 3TYL's pence line is stored in pounds, used as it is.
        self.assertAlmostEqual(data.prices["bonds3x"][-1], 20.0, places=6)
        result = study.run(data, "1y", 1000, 0)
        self.assertTrue(any("3x SPY" in n for n in result["simulated_notes"]))
        self.assertIn("quarter", " ".join(study.how_it_works(data)))


def _advice(**over):
    args = dict(start_date=date(2025, 1, 10), as_of=date(2025, 3, 31), currency="GBP",
                equity_symbol="ISP6.L", safe_symbol="ERNS.L", equity_qty=D("80"), equity_price=D("10"),
                safe_qty=D("20"), safe_price=D("10"), cash=D("0"), starting_equity_cost=D("800"),
                spy_reference=())
    args.update(over)
    alerts = three.advice_for_holdings(**args)
    return alerts, next(a for a in alerts if a["kind"] == "QUARTERLY")


class ThreeSigRuleTests(SimpleTestCase):
    def test_three_percent_line_plus_half_of_deposits(self):
        self.assertEqual(three.signal_line_value(
            start_date=date(2025, 1, 10), as_of=date(2025, 3, 31), starting_equity_cost=D("800")), D("824.00"))
        self.assertEqual(three.signal_line_value(
            start_date=date(2025, 1, 10), as_of=date(2025, 6, 30), starting_equity_cost=D("800"),
            contributions=((date(2025, 5, 1), D("100")),)), D("898.72"))

    def test_below_the_line_buys_stock_with_bonds(self):
        _, plan = _advice()
        self.assertEqual([(a["side"], a["symbol"]) for a in plan["actions"]],
                         [("SELL", "ERNS.L"), ("BUY", "ISP6.L")])
        self.assertIn("£824.00", plan["message"])
        self.assertEqual(plan["reset_reason"], "")

    def test_above_the_line_sells_stock_into_bonds(self):
        _, plan = _advice(equity_price=D("11"))
        self.assertEqual([(a["side"], a["symbol"]) for a in plan["actions"]],
                         [("SELL", "ISP6.L"), ("BUY", "ERNS.L")])

    def test_bonds_above_30_percent_ask_for_an_80_20_reset(self):
        _, plan = _advice(equity_price=D("5"))
        self.assertIn("30%", plan["reset_reason"])
        self.assertIn("review a reset to 80%", plan["message"])
        buy = next(a for a in plan["actions"] if a["side"] == "BUY")
        self.assertEqual(buy["symbol"], "ISP6.L")
        # 80% of 600 = 480 of stock, from 400: about 80 more.
        self.assertLessEqual(D(buy["estimated_gbp"]), D("80"))

    def test_30_down_skips_two_sells_then_resets(self):
        closes = [(_quarter_close(quarter_number(date(2025, 3, 31)) - k), D("100")) for k in range(1, 9)]
        closes.append((date(2025, 3, 31), D("65")))
        alerts, plan = _advice(equity_price=D("11"), spy_reference=closes)
        self.assertTrue(any(a["kind"] == "THIRTY_DOWN" for a in alerts))
        self.assertEqual([a["side"] for a in plan["actions"]], ["HOLD"])
        self.assertIn("defer this stock sell", plan["message"])

        class Row:
            actions, message = plan["actions"], plan["message"]
        self.assertEqual(count_skipped_equity_sells([Row]), 1)
        _, third = _advice(equity_price=D("11"), spy_reference=closes,
                           prior_skipped_sells=2, prior_drawdown=True, down_age_quarters=2)
        self.assertIn("Two sell signals", third["reset_reason"])
        _, expired = _advice(prior_drawdown=True, down_age_quarters=8)
        self.assertIn("expired", expired["reset_reason"])

    def test_throttle_and_all_in(self):
        # Shortfall of 824 - 640 = 184, reserve 200: between 90% and 100%, so 81%.
        _, plan = _advice(equity_price=D("8"))
        buy = next(a for a in plan["actions"] if a["side"] == "BUY")
        self.assertLessEqual(D(buy["estimated_gbp"]), D("162"))
        self.assertIn("throttle", plan["message"])
        # A shortfall above the whole reserve uses it all and suggests a reset review.
        _, plan = _advice(equity_price=D("7.6"), safe_qty=D("17.5"))
        self.assertEqual(plan["reserve_empty_review_notice"], three.RESERVE_EMPTY_NOTICE)
        self.assertEqual(plan["reset_reason"], "")

    def test_unsupported_input_gives_nothing(self):
        self.assertEqual(three.advice_for_holdings(**dict(
            start_date=date(2025, 1, 10), as_of=date(2025, 3, 31), currency="JPY",
            equity_symbol="E", safe_symbol="S", equity_qty=D("1"), equity_price=D("1"),
            safe_qty=D("1"), safe_price=D("1"), cash=D("0"), starting_equity_cost=D("1"))), [])

    def test_catalogue_and_plugin(self):
        rows = [(r.role, r.ticker) for r in catalogue.rows_for("three-sig", "TRADING212", "ISA")]
        self.assertEqual(rows, [("smallcap", "ISP6.L"), ("safe", "ERNS.L")])
        rows = [(r.role, r.ticker) for r in catalogue.rows_for("three-sig", "FIDELITY", "BROKERAGE")]
        self.assertEqual(rows, [("smallcap", "IJR"), ("safe", "AGG")])
        rows = [(r.role, r.ticker) for r in catalogue.rows_for("three-sig", "TRADE_REPUBLIC", "DEPOT")]
        self.assertEqual(rows, [("smallcap", "IUS3.DE"), ("safe", "ERNX.DE")])
        for currency in ("GBP", "USD", "EUR"):
            self.assertEqual(catalogue.released_version("three-sig", currency), three.VERSION)
        plugin = module_for_key("three-sig")
        self.assertTrue(plugin.supports_reset)
        self.assertEqual(plugin.reset_equity_fraction, D("0.80"))
        self.assertEqual(plugin.reference_symbols, ("SPY",))


class ThreeSigLiveReviewTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user("usd-3sig", password="a-long-test-password-1")
        self.client.force_login(owner)
        self.end = quarter_end_before(timezone.localdate())
        self.begin = date(self.end.year, self.end.month - 2, 1)
        self.client.post(reverse("v2_add_strategy"), {
            "strategy": "three-sig", "platform": "FIDELITY", "account_type": "BROKERAGE",
            "ack": "yes", "mode": "fresh", "name": "3Sig US", "start": self.begin.isoformat(),
            "amount": "1000", "deposited": "yes", "go_live": "yes"})
        self.strategy = StrategyInstance.objects.get(owner=owner)
        q = quarter_number(self.end)
        for offset in range(1, 9):
            MarketObservation.objects.create(symbol="SPY", as_of=_quarter_close(q - offset),
                                             price=D("100"), currency="USD")

    def test_quarterly_review_without_tqqq(self):
        strategy = self.strategy
        self.assertEqual((strategy.account.currency, strategy.strategy_version), ("USD", three.VERSION))
        self.assertEqual((strategy.equity_symbol, strategy.safe_symbol), ("IJR", "AGG"))
        self.assertFalse(strategy.tracking_only)
        first = self.begin + timedelta(days=1)
        validate_and_record(strategy, LedgerEntry(kind="BUY", symbol="IJR", quantity=D("8"),
                                                  unit_price=D("100"), occurred_on=first))
        validate_and_record(strategy, LedgerEntry(kind="BUY", symbol="AGG", quantity=D("2"),
                                                  unit_price=D("100"), occurred_on=first))
        cached = {}
        for symbol, price in (("IJR", D("95")), ("SPY", D("98")), ("AGG", D("100"))):
            MarketObservation.objects.create(symbol=symbol, as_of=self.end, price=price, currency="USD")
            cached[symbol] = MarketObservation.objects.create(
                symbol=symbol, as_of=timezone.localdate(), price=price, currency="USD")
        # No TQQQ is fetched or required.
        with patch("tracker.market_signals.sync_ticker", side_effect=lambda s: cached[s]), \
                patch("tracker.market_signals.dispatch"):
            result = review_strategy(strategy)
        self.assertEqual(result["status"], "ok", result)
        signal = StrategySignal.objects.get(strategy=strategy, kind="QUARTERLY")
        self.assertEqual(signal.strategy_version, three.VERSION)
        self.assertIn("3Sig quarterly review", signal.title)
        self.assertTrue(any(a["side"] == "BUY" and a["symbol"] == "IJR" for a in signal.actions))
        self.assertIn("$", signal.message)
        page = self.client.get(reverse("v2_instance_page", args=[strategy.pk, "review"]))
        self.assertEqual(page.status_code, 200)

    def test_pound_setup_pins_isp6_and_erns(self):
        owner = get_user_model().objects.create_user("gbp-3sig", password="a-long-test-password-1")
        self.client.force_login(owner)
        self.client.post(reverse("v2_add_strategy"), {
            "strategy": "three-sig", "platform": "TRADING212", "account_type": "ISA",
            "ack": "yes", "mode": "fresh", "name": "3Sig UK",
            "start": (timezone.localdate() - timedelta(days=5)).isoformat(),
            "amount": "1000", "deposited": "yes", "go_live": "yes"})
        strategy = StrategyInstance.objects.get(owner=owner)
        self.assertEqual((strategy.account.currency, strategy.strategy_version), ("GBP", three.VERSION))
        self.assertEqual((strategy.equity_symbol, strategy.safe_symbol), ("ISP6.L", "ERNS.L"))


class ThreeSigStudyTests(TestCase):
    def test_dollar_study_replays_the_rules_and_counts_resets(self):
        cache.clear()
        start = TODAY - timedelta(days=900)
        seed("SPY", start, lambda d, i: 200.0)
        seed("VTI", start, lambda d, i: 50.0)
        # Small caps double; the surplus sold into bonds takes them past 30%, so a reset follows.
        jump = TODAY - timedelta(days=300)
        seed("IJR", start, lambda d, i: 100.0 if d < jump else 200.0)
        seed("AGG", start, lambda d, i: 100.0)
        study = module_for_key("three-sig").study["USD"]
        data = study.load_data()
        self.assertTrue(data.ready)
        result = study.run(data, "1y", 1000, 0)
        self.assertIsNotNone(result["series"]["strategy"])
        self.assertGreaterEqual(result["assumed_resets"], 1)
        self.assertEqual(result["reset_mix"], "80/20")
        self.assertIn("treated as confirmed", " ".join(study.how_it_works(data)))

    def test_pound_study_follows_ijr_before_isp6(self):
        cache.clear()
        start = TODAY - timedelta(days=600)
        seed("GBPUSD=X", start, lambda d, i: 1.25)
        seed("SPY", start, lambda d, i: 200.0)
        seed("VTI", start, lambda d, i: 50.0)
        seed("IJR", start, lambda d, i: 100.0)
        seed("ISP6.L", TODAY - timedelta(days=100), lambda d, i: 80.0, currency="GBP")
        seed("ERNS.L", start, lambda d, i: 100.0, currency="GBP")
        study = module_for_key("three-sig").study["GBP"]
        data = study.load_data()
        result = study.run(data, "1y", 1000, 100)
        self.assertTrue(any("IJR" in n for n in result["simulated_notes"]))
        self.assertEqual(len(result["series"]["strategy"]["values"]), len(result["dates"]))
