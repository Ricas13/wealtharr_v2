"""Independent boundary tests for the advisory engine and Yahoo normalization."""
import json
from datetime import date
from decimal import Decimal as D
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase

from tracker.market_signals import advice_for_holdings, fetch_chart, quarter_number, prior_quarter_name

def evaluate(*, as_of=date(2026, 4, 2), reference=(), cash=D("100"), safe=D("5"),
             equity_price=D("40"), equity_qty=D("10"), starting=D("500")):
    return advice_for_holdings(
        start_date=date(2026, 1, 12), as_of=as_of, currency="GBP",
        equity_symbol="LQQ3.L", safe_symbol="AGBP.L",
        equity_qty=equity_qty, equity_price=equity_price,
        safe_qty=safe, safe_price=D("100"), cash=cash,
        starting_equity_cost=starting, reference=list(reference),
    )

class NineSigCalculations(SimpleTestCase):
    def test_year_rollover(self):
        self.assertEqual(quarter_number(date(2026, 1, 1)) - quarter_number(date(2025, 12, 31)), 1)
        self.assertEqual(prior_quarter_name(date(2026, 1, 1)), "2025-Q4")

    def test_no_initial_quarter_review(self):
        self.assertEqual(evaluate(as_of=date(2026, 2, 2)), [])

    def test_quarterly_review_is_pinned_to_quarter(self):
        signals = evaluate()
        quarterly = next(s for s in signals if s["kind"] == "QUARTERLY")
        self.assertEqual(quarterly["key"], "quarter:2026-Q1")
        self.assertTrue(any(a["side"] == "BUY" for a in quarterly["actions"]))

    def test_30_down_sells_half_once_per_observed_peak(self):
        history = [(date(2026, 1, 13), D("100")), (date(2026, 3, 4), D("69"))]
        signals = evaluate(as_of=date(2026, 3, 4), reference=history)
        warning = next(s for s in signals if s["kind"] == "THIRTY_DOWN")
        self.assertEqual(warning["key"], "30down:2026-01-13")
        self.assertEqual(warning["actions"][0]["quantity"], "5.0000")

    def test_drop_of_just_under_30_percent_does_not_warn(self):
        history = [(date(2026, 1, 13), D("100")), (date(2026, 3, 4), D("70.01"))]
        self.assertFalse(any(x["kind"] == "THIRTY_DOWN"
                             for x in evaluate(as_of=date(2026, 3, 4), reference=history)))

    def test_safe_asset_cash_reserve_limits_proposed_buy(self):
        signals = evaluate(cash=D("0"), safe=D("2"), equity_price=D("30"), starting=D("1000"))
        q = next(x for x in signals if x["kind"] == "QUARTERLY")
        safe_sales = sum(D(a["estimated_gbp"]) for a in q["actions"] if a["symbol"] == "AGBP.L")
        self.assertLessEqual(safe_sales, D("180"))
        equity_buys = sum(D(a["estimated_gbp"]) for a in q["actions"] if a["side"] == "BUY")
        self.assertLessEqual(equity_buys, safe_sales)

    def test_unfunded_or_missing_equity_never_proposes_a_trade(self):
        self.assertEqual(evaluate(equity_qty=D("0")), [])
        self.assertEqual(evaluate(starting=D("0")), [])

class YahooParsing(SimpleTestCase):
    @patch("tracker.market_signals.urlopen")
    def test_pence_prices_are_normalized_to_pounds(self, open_url):
        epoch = 1577923200  # 2 January 2020 UTC
        content = json.dumps({"chart": {"result": [{
            "meta": {"currency": "GBp"},
            "timestamp": [epoch],
            "indicators": {"quote": [{"close": [1234.0]}]},
        }]}}).encode("utf-8")
        response = MagicMock()
        response.status = 200
        response.read.return_value = content
        open_url.return_value.__enter__.return_value = response
        currency, rows = fetch_chart("LQQ3.L", date(2020, 1, 1))
        self.assertEqual(currency, "GBP")
        self.assertEqual(rows[0][1], D("12.34000000"))

    def test_unsafe_ticker_is_rejected_before_any_http_request(self):
        with self.assertRaises(ValueError):
            fetch_chart("https://evil.invalid", date(2020, 1, 1))


from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from tracker.models import Account, LedgerEntry, MarketObservation, PriceMark, StrategyInstance, StrategyPreset, StrategySignal
from tracker.services import validate_and_record
from tracker.market_signals import review_strategy


class MarketWorkflowTests(TestCase):
    def setUp(self):
        user = get_user_model()
        self.owner = user.objects.create_user(username="monitor-owner", email="monitor@example.com",
                                               password="A-long-test-password-123")
        self.other = user.objects.create_user(username="monitor-other", email="other@example.com",
                                               password="A-long-test-password-123")
        self.account = Account.objects.create(owner=self.owner, name="ISA monitoring", kind="ISA")
        self.preset = StrategyPreset.objects.get(slug="nasdaq-quarterly-target")
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=self.account, preset=self.preset,
            name="9Sig monitored", equity_symbol="LQQ3.L", safe_symbol="AGBP.L", market_data_enabled=True,
        )

    def test_only_owner_can_update_monitoring_settings_or_acknowledge(self):
        url = reverse("save_automation", args=[self.strategy.pk])
        self.client.force_login(self.other)
        response = self.client.post(url, {
            "equity_symbol": "CHANGED.L", "safe_symbol": "AGBP.L", "market_data_enabled": "on",
        })
        self.assertEqual(response.status_code, 404)
        self.strategy.refresh_from_db()
        self.assertEqual(self.strategy.equity_symbol, "LQQ3.L")
        self.client.force_login(self.owner)
        response = self.client.post(url, {
            "equity_symbol": " LQQ3.L ", "safe_symbol": " AGBP.L ",
            "market_data_enabled": "on",
        })
        self.assertEqual(response.status_code, 302)
        self.strategy.refresh_from_db()
        self.assertTrue(self.strategy.market_data_enabled)
        self.assertEqual(self.strategy.equity_symbol, "LQQ3.L")

    def test_review_creates_and_deduplicates_persistent_notifications(self):
        start = timezone.localdate() - __import__("datetime").timedelta(days=95)
        now = timezone.localdate()
        validate_and_record(self.strategy, LedgerEntry(
            kind="DEPOSIT", occurred_on=start, amount=D("2000"),
        ))
        validate_and_record(self.strategy, LedgerEntry(
            kind="BUY", occurred_on=start, symbol="LQQ3.L", quantity=D("10"), unit_price=D("50"),
        ))
        validate_and_record(self.strategy, LedgerEntry(
            kind="BUY", occurred_on=start, symbol="AGBP.L", quantity=D("5"), unit_price=D("80"),
        ))
        MarketObservation.objects.create(symbol="TQQQ", as_of=start, price=D("100"), currency="USD")
        reference = MarketObservation.objects.create(
            symbol="TQQQ", as_of=now, price=D("65"), currency="USD",
        )
        equity = MarketObservation.objects.create(
            symbol="LQQ3.L", as_of=now, price=D("48"), currency="GBP",
        )
        safe = MarketObservation.objects.create(
            symbol="AGBP.L", as_of=now, price=D("81"), currency="GBP",
        )
        mapped = {"TQQQ": reference, "LQQ3.L": equity, "AGBP.L": safe}
        with patch("tracker.market_signals.sync_ticker", side_effect=lambda symbol: mapped[symbol]), \
             patch("tracker.market_signals.dispatch") as dispatcher:
            first = review_strategy(self.strategy)
            second = review_strategy(self.strategy)
        self.assertEqual(first["status"], "ok")
        self.assertGreaterEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        self.assertEqual(dispatcher.call_count, first["created"])
        self.assertEqual(
            StrategySignal.objects.filter(strategy=self.strategy, kind="THIRTY_DOWN").count(), 1
        )
        self.assertEqual(PriceMark.objects.get(strategy=self.strategy, symbol="LQQ3.L").source, "yahoo")

        signal = StrategySignal.objects.get(strategy=self.strategy, kind="THIRTY_DOWN")
        url = reverse("acknowledge_signal", args=[self.strategy.pk, signal.pk])
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url).status_code, 404)
        signal.refresh_from_db()
        self.assertIsNone(signal.acknowledged_at)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(url).status_code, 302)
        signal.refresh_from_db()
        self.assertIsNotNone(signal.acknowledged_at)
