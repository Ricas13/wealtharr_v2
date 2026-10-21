"""Dollar (Fidelity) and euro (Trade Republic) accounts: setup, prices, reviews, pages, Study."""
from datetime import date, timedelta
from decimal import Decimal as D
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.core import catalogue
from tracker.models import MarketObservation, NotificationPreference, StrategyInstance, StrategySignal


class CatalogueByCurrencyTests(TestCase):
    def test_each_broker_gets_its_own_currency_and_exact_lines(self):
        us = catalogue.rows_for("all-weather", "FIDELITY", "ROTH_IRA")
        self.assertEqual([r.ticker for r in us], ["VTI", "TLT", "IEF", "GLD", "DBC"])
        self.assertTrue(all(r.currency == "USD" and r.raw_quote_unit == "USD" for r in us))
        eu = catalogue.rows_for("dual-momentum", "TRADE_REPUBLIC", "DEPOT")
        self.assertEqual([r.ticker for r in eu], ["SXR8.DE", "EXUS.DE", "VAGF.DE"])
        self.assertTrue(all(r.currency == "EUR" for r in eu))
        self.assertEqual(catalogue.rows_for("dual-momentum", "FIDELITY", "BROKERAGE")[1].isin, "US4642882405")
        manifest = catalogue.onboarding_manifest("golden-butterfly", "FIDELITY", "BROKERAGE")
        self.assertEqual((manifest["country"], manifest["currency"]), ("US", "USD"))
        self.assertEqual(manifest["roles"]["scv"]["raw_to_account_factor"], "1")
        # No evidence that Trade Republic carries every All Weather line yet: not offered.
        self.assertFalse(catalogue.selection_available("all-weather", "TRADE_REPUBLIC", "DEPOT"))
        # A pound manifest keeps its original shape (no currency key).
        self.assertNotIn("currency", catalogue.onboarding_manifest("all-weather", "TRADING212", "ISA"))
        # Accounts types belong to their broker.
        self.assertFalse(catalogue.selection_available("all-weather", "FIDELITY", "ISA"))

    def test_dollar_lines_are_not_converted_and_euro_quotes_are_accepted(self):
        from tracker.market_data import FetchedChart, sync_ticker, usd_holding_symbols
        self.assertNotIn("VTI", usd_holding_symbols())
        self.assertIn("USSC.L", usd_holding_symbols())
        day = timezone.localdate() - timedelta(days=1)
        charts = {"VTI": FetchedChart("USD", [(day, D("250"))], "USD", {day: D("250")}),
                  "SXR8.DE": FetchedChart("EUR", [(day, D("560"))], "EUR", {day: D("560")})}
        with patch("tracker.market_data.fetch_chart", side_effect=lambda s, since: charts[s]):
            sync_ticker("VTI", force=True)
            sync_ticker("SXR8.DE", force=True)
        vti = MarketObservation.objects.get(symbol="VTI")
        self.assertEqual((vti.price, vti.currency, vti.raw_unit_verified), (D("250"), "USD", True))
        self.assertEqual(MarketObservation.objects.get(symbol="SXR8.DE").currency, "EUR")


class DollarAccountEndToEndTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_user("usd-owner", password="a-long-test-password-1")
        NotificationPreference.objects.create(owner=self.owner, email_enabled=True,
                                              destination_email="o@example.invalid",
                                              discord_enabled=False, monthly_reminders=False)
        self.client.force_login(self.owner)
        self.today = timezone.localdate()
        self.year_end = date(self.today.year - 1, 12, 31)
        self.recent = self.today - timedelta(days=1)
        if self.recent <= self.year_end + timedelta(days=2):
            self.skipTest("Needs a few days after the year end.")

    def observe(self, day, prices, currency):
        for symbol, price in prices.items():
            MarketObservation.objects.update_or_create(
                symbol=symbol, as_of=day, defaults={"price": D(price), "currency": currency})

    def latest(self, symbol, **kwargs):
        return MarketObservation.objects.filter(symbol=symbol).order_by("-as_of").first()

    def test_all_weather_at_fidelity_in_dollars(self):
        start = self.year_end - timedelta(days=180)
        page = self.client.get(reverse("v2_add_strategy") + "?strategy=all-weather&platform=FIDELITY")
        self.assertContains(page, "Roth IRA")
        self.assertContains(page, "Money deposited ($)")
        self.assertContains(page, "US9229087690")
        self.assertNotContains(page, "Stocks &amp; Shares ISA")
        self.client.post(reverse("v2_add_strategy"), {
            "strategy": "all-weather", "platform": "FIDELITY", "account_type": "ROTH_IRA", "ack": "yes",
            "mode": "fresh", "name": "AW US", "start": start.isoformat(), "amount": "10000",
            "deposited": "yes", "go_live": "yes"})
        strategy = StrategyInstance.objects.get(owner=self.owner)
        self.assertEqual((strategy.account.currency, strategy.account.country, strategy.account.kind),
                         ("USD", "US", "ROTH_IRA"))
        self.assertFalse(strategy.tracking_only)
        buys = {"VTI": ("12", "250"), "TLT": ("44", "90"), "IEF": ("15", "100"),
                "GLD": ("3", "250"), "DBC": ("33", "22.7")}
        for symbol, (qty, price) in buys.items():
            self.client.post(reverse("v2_ledger_add", args=[strategy.pk]), {
                "intent": "save", "kind": "BUY", "symbol": symbol, "quantity": qty, "unit_price": price,
                "fee": "0", "occurred_on": start.isoformat()})
        closes = {"VTI": "400", "TLT": "90", "IEF": "100", "GLD": "250", "DBC": "22.7"}
        self.observe(self.year_end, closes, "USD")
        self.observe(self.recent, closes, "USD")
        with patch("tracker.market_data.sync_ticker", side_effect=self.latest), \
                patch("tracker.management.commands.sync_market_and_signals.sync_ticker"):
            call_command("sync_market_and_signals", verbosity=0)
        signal = StrategySignal.objects.get(strategy=strategy)
        self.assertEqual({a["symbol"]: a["side"] for a in signal.actions}["VTI"], "SELL")
        today_page = self.client.get(reverse("v2_instance_page", args=[strategy.pk, "today"]))
        self.assertContains(today_page, "$")
        self.assertNotContains(today_page, "£")
        review = reverse("v2_guided_review", args=[strategy.pk, signal.pk])
        for index, action in enumerate(signal.actions):
            response = self.client.post(review, {
                "intent": "record_one", "action_index": str(index),
                "executed_on": (self.year_end + timedelta(days=2)).isoformat(),
                "quantity": action["quantity"], "unit_price": closes[action["symbol"]], "fee": "0",
                "idempotency_key": str(uuid4()), "actual_confirmation": "yes"})
            self.assertEqual(response.status_code, 302, response.context and response.context.get("review_error"))
        from tracker.review_autofinish import auto_finish_reviews
        self.assertEqual(auto_finish_reviews(), 1)
        # Overview with a pound strategy too: dollars are listed, not added to pounds.
        from tracker.models import Account, StrategyPreset
        preset, _ = StrategyPreset.objects.get_or_create(slug="all-weather", defaults={"name": "All Weather"})
        uk = Account.objects.create(owner=self.owner, name="UK", kind="ISA", broker="TRADING212")
        StrategyInstance.objects.create(owner=self.owner, account=uk, preset=preset, name="AW UK",
                                        strategy_key="all-weather")
        overview = self.client.get(reverse("v2_overview"))
        self.assertContains(overview, "In another currency, not combined")


class EuroAccountTests(TestCase):
    def test_dual_momentum_at_trade_republic_in_euros(self):
        owner = get_user_model().objects.create_user("eur-owner", password="a-long-test-password-1")
        self.client.force_login(owner)
        start = timezone.localdate() - timedelta(days=20)
        self.client.post(reverse("v2_add_strategy"), {
            "strategy": "dual-momentum", "platform": "TRADE_REPUBLIC", "account_type": "DEPOT", "ack": "yes",
            "mode": "fresh", "name": "DM EU", "start": start.isoformat(), "amount": "5000",
            "deposited": "yes"})
        strategy = StrategyInstance.objects.get(owner=owner)
        self.assertEqual((strategy.account.currency, strategy.account.country), ("EUR", "DE"))
        ledger = self.client.get(reverse("v2_instance_page", args=[strategy.pk, "ledger"]))
        self.assertContains(ledger, "€5,000")
        from tracker.strategies.dual_momentum.plugin import cash_symbol
        self.assertEqual(cash_symbol(strategy), "XEON.DE")


class StudyByCurrencyTests(TestCase):
    def test_dollar_study_uses_dollar_funds_without_conversion(self):
        from tracker.strategies.registry import module_for_key
        from tracker.test_study import TODAY, seed
        cache.clear()
        start = TODAY - timedelta(days=800)
        for symbol, price in (("SPY", 400.0), ("VTI", 200.0), ("TLT", 90.0), ("IEF", 100.0),
                              ("GLD", 250.0), ("DBC", 22.0)):
            seed(symbol, start, lambda d, i, p=price: p)
        study = module_for_key("all-weather").study["USD"]
        data = study.load_data()
        self.assertTrue(data.ready)
        self.assertEqual(data.prices["stocks"][0], 200.0)
        result = study.run(data, "1y", 1000, 0)
        self.assertEqual(result["simulated_notes"], [])
        self.assertAlmostEqual(result["series"]["strategy"]["final"], 1000, delta=1)
