"""9Sig in dollar (Fidelity: TQQQ + AGG) and euro (Trade Republic: 3QQQ + ERNX) accounts.

Pound accounts keep rule version v2. Dollar and euro accounts pin v3, which runs
exactly the same v2 rules with amounts in the account currency.
"""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.core import catalogue
from tracker.market_signals import quarter_end_before, quarter_number, review_strategy
from tracker.models import LedgerEntry, MarketObservation, StrategyInstance, StrategySignal
from tracker.services import validate_and_record
from tracker.strategies.nine_sig import v2, v3


class RuleVersionTests(SimpleTestCase):
    def test_v3_is_v2_for_any_supported_currency(self):
        args = dict(start_date=date(2025, 1, 10), as_of=date(2025, 6, 30),
                    equity_symbol="E", safe_symbol="S", equity_qty=D("10"), equity_price=D("50"),
                    safe_qty=D("4"), safe_price=D("100"), cash=D("0"),
                    starting_equity_cost=D("600"), reference=(), spy_reference=())
        pounds = v2.advice_for_holdings(currency="GBP", **args)
        self.assertEqual(v3.advice_for_holdings(currency="GBP", **args), pounds)
        dollars = v3.advice_for_holdings(currency="USD", **args)
        self.assertEqual([r["actions"] for r in dollars], [r["actions"] for r in pounds])
        self.assertTrue(all("£" not in r["message"] for r in dollars))
        self.assertEqual(v2.advice_for_holdings(currency="USD", **args), [])
        self.assertEqual(v3.advice_for_holdings(currency="JPY", **args), [])

    def test_versions_per_currency(self):
        self.assertEqual(catalogue.released_version("nasdaq-quarterly-target"), v2.VERSION)
        self.assertEqual(catalogue.released_version("nasdaq-quarterly-target", "USD"), v3.VERSION)
        self.assertEqual(catalogue.released_version("nasdaq-quarterly-target", "EUR"), v3.VERSION)
        rows = {r.role: r.ticker for r in catalogue.rows_for("nasdaq-quarterly-target", "FIDELITY", "BROKERAGE")}
        self.assertEqual(rows, {"growth": "TQQQ", "safe": "AGG"})
        rows = {r.role: r.ticker for r in catalogue.rows_for("nasdaq-quarterly-target", "TRADE_REPUBLIC", "DEPOT")}
        self.assertEqual(rows, {"growth": "3QQQ.DE", "safe": "ERNX.DE"})


class DollarNineSigTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user("usd-9sig", password="a-long-test-password-1")
        self.client.force_login(owner)
        self.end = quarter_end_before(timezone.localdate())
        self.begin = date(self.end.year, self.end.month - 2, 1)
        start = self.begin
        self.client.post(reverse("v2_add_strategy"), {
            "strategy": "nasdaq-quarterly-target", "platform": "FIDELITY", "account_type": "BROKERAGE",
            "ack": "yes", "mode": "fresh", "name": "9Sig US", "start": start.isoformat(),
            "amount": "1000", "deposited": "yes", "go_live": "yes"})
        self.strategy = StrategyInstance.objects.get(owner=owner)
        q = quarter_number(self.end)
        for offset in range(1, 9):
            y, index = divmod(q - offset, 4)
            month = (index + 1) * 3
            close = date(y, month, monthrange(y, month)[1])
            MarketObservation.objects.create(symbol="SPY", as_of=close, price=D("100"), currency="USD")
            if offset == 1:
                MarketObservation.objects.create(symbol="TQQQ", as_of=close, price=D("80"), currency="USD")

    def test_quarterly_review_in_dollars_with_v3(self):
        strategy = self.strategy
        self.assertEqual((strategy.account.currency, strategy.strategy_version), ("USD", v3.VERSION))
        self.assertEqual((strategy.equity_symbol, strategy.safe_symbol), ("TQQQ", "AGG"))
        self.assertFalse(strategy.tracking_only)
        first = self.begin + timedelta(days=1)
        validate_and_record(strategy, LedgerEntry(kind="BUY", symbol="TQQQ", quantity=D("10"),
                                                  unit_price=D("60"), occurred_on=first))
        validate_and_record(strategy, LedgerEntry(kind="BUY", symbol="AGG", quantity=D("4"),
                                                  unit_price=D("100"), occurred_on=first))
        cached = {}
        for symbol, price in (("TQQQ", D("50")), ("SPY", D("98")), ("AGG", D("100"))):
            MarketObservation.objects.create(symbol=symbol, as_of=self.end, price=price, currency="USD")
            cached[symbol] = MarketObservation.objects.create(
                symbol=symbol, as_of=timezone.localdate(), price=price, currency="USD")
        with patch("tracker.market_signals.sync_ticker", side_effect=lambda s: cached[s]), \
                patch("tracker.market_signals.dispatch"):
            result = review_strategy(strategy)
        self.assertEqual(result["status"], "ok", result)
        signal = StrategySignal.objects.get(strategy=strategy, kind="QUARTERLY")
        self.assertEqual(signal.strategy_version, v3.VERSION)
        self.assertTrue(any(a["side"] == "BUY" and a["symbol"] == "TQQQ" for a in signal.actions))
        self.assertIn("$", signal.message)
        self.assertNotIn("£", signal.message)
        page = self.client.get(reverse("v2_instance_page", args=[strategy.pk, "review"]))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Pinned strategy or mapping version does not match")


class EuroNineSigTests(TestCase):
    def test_setup_at_trade_republic_pins_v3_and_euro_lines(self):
        owner = get_user_model().objects.create_user("eur-9sig", password="a-long-test-password-1")
        self.client.force_login(owner)
        self.client.post(reverse("v2_add_strategy"), {
            "strategy": "nasdaq-quarterly-target", "platform": "TRADE_REPUBLIC", "account_type": "DEPOT",
            "ack": "yes", "mode": "fresh", "name": "9Sig EU",
            "start": (timezone.localdate() - timedelta(days=5)).isoformat(),
            "amount": "1000", "deposited": "yes", "go_live": "yes"})
        strategy = StrategyInstance.objects.get(owner=owner)
        self.assertEqual((strategy.account.currency, strategy.strategy_version), ("EUR", v3.VERSION))
        self.assertEqual((strategy.equity_symbol, strategy.safe_symbol), ("3QQQ.DE", "ERNX.DE"))
        self.assertFalse(strategy.tracking_only)


class NineSigStudyByCurrencyTests(TestCase):
    def test_dollar_study_uses_tqqq_and_agg_without_conversion(self):
        from django.core.cache import cache
        from tracker.strategies.registry import module_for_key
        from tracker.test_study import TODAY, seed
        cache.clear()
        start = TODAY - timedelta(days=500)
        seed("QQQ", start, lambda d, i: 100 * (1.0005 ** i))
        seed("SPY", start, lambda d, i: 200.0)
        seed("VTI", start, lambda d, i: 50.0)
        seed("TQQQ", TODAY - timedelta(days=200), lambda d, i: 40.0)
        seed("AGG", start, lambda d, i: 100.0)
        study = module_for_key("nasdaq-quarterly-target").study["USD"]
        self.assertEqual(study.CURRENCY, "USD")
        data = study.load_data()
        self.assertTrue(data.ready)
        self.assertEqual(data.spy[0], 200.0)
        self.assertEqual(data.safe[-1], 100.0)
        result = study.run(data, "1y", 1000, 0)
        self.assertTrue(any("rebuilt from 3x QQQ" in note for note in result["simulated_notes"]))
        self.assertIn("US dollars", " ".join(study.how_it_works(data)))
