"""Independent boundary tests for the advisory engine and Yahoo normalization."""
import json
from calendar import monthrange
from datetime import date
from decimal import Decimal as D
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase

from tracker.market_signals import advice_for_holdings, fetch_chart, quarter_number, prior_quarter_name

def evaluate(*, as_of=date(2026, 4, 2), reference=(), spy_reference=(), cash=D("100"), safe=D("5"),
             equity_price=D("40"), equity_qty=D("10"), starting=D("500")):
    return advice_for_holdings(
        start_date=date(2026, 1, 12), as_of=as_of, currency="GBP",
        equity_symbol="LQQ3.L", safe_symbol="AGBP.L",
        equity_qty=equity_qty, equity_price=equity_price,
        safe_qty=safe, safe_price=D("100"), cash=cash,
        starting_equity_cost=starting, reference=list(reference), spy_reference=list(spy_reference),
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

    def test_30_down_warns_without_instructing_a_half_sale(self):
        spy = [(date(2025, 12, 31), D("100")), (date(2026, 3, 31), D("65"))]
        signals = evaluate(as_of=date(2026, 3, 31), spy_reference=spy,
                           equity_price=D("70"))
        warning = next(s for s in signals if s["kind"] == "THIRTY_DOWN")
        self.assertEqual(warning["actions"], [])
        quarterly = next(s for s in signals if s["kind"] == "QUARTERLY")
        self.assertEqual(quarterly["actions"][0]["side"], "HOLD")

    def test_drop_just_under_threshold_does_not_warn(self):
        spy = [(date(2025, 12, 31), D("100")), (date(2026, 3, 31), D("70.01"))]
        self.assertFalse(any(x["kind"] == "THIRTY_DOWN" for x in evaluate(
            as_of=date(2026, 3, 31), spy_reference=spy)))

    def test_workbook_first_quarter_target_is_nine_percent_not_annual(self):
        # DEMO 2010++: AV3 60000; AV4 65400 = 60000 * 1.09.
        signals = advice_for_holdings(
            start_date=date(2026, 1, 1), as_of=date(2026, 3, 31),
            currency="GBP", equity_symbol="LQQ3.L", safe_symbol="AGBP.L",
            equity_qty=D("150000"), equity_price=D("0.29"),
            safe_qty=D("382"), safe_price=D("104.2"), cash=D("69.54"),
            starting_equity_cost=D("60000"), reference=[])
        quarter = next(x for x in signals if x["kind"] == "QUARTERLY")
        self.assertIn("£65400.00", quarter["message"])
        self.assertTrue(any(x["side"] == "BUY" and x["symbol"] == "LQQ3.L"
                            for x in quarter["actions"]))

    def test_spike_reset_requires_two_verified_quarter_closes(self):
        refs = [(date(2025, 12, 31), D("50")), (date(2026, 3, 31), D("100"))]
        out = evaluate(as_of=date(2026, 3, 31), reference=refs)
        self.assertIn("doubled", next(x["message"] for x in out if x["kind"] == "QUARTERLY"))

    def test_30_down_two_skipped_sells_requires_reset_review(self):
        spy = [(date(2025, 12, 31), D("100")), (date(2026, 3, 31), D("60"))]
        out = advice_for_holdings(start_date=date(2026, 1, 12),
            as_of=date(2026, 3, 31), currency="GBP",
            equity_symbol="LQQ3.L", safe_symbol="AGBP.L",
            equity_qty=D("10"), equity_price=D("100"), safe_qty=D("5"),
            safe_price=D("100"), cash=D("100"),
            starting_equity_cost=D("500"), spy_reference=spy,
            reference=[], prior_skipped_sells=2, prior_drawdown=True)
        q = next(x for x in out if x["kind"] == "QUARTERLY")
        self.assertIn("Two sell signals", q["message"])
        self.assertFalse(any(x["side"] == "HOLD" for x in q["actions"]))

    def test_safe_asset_cash_reserve_limits_proposed_buy(self):
        # v2 owner decision 3: over-reserve shortfall sells ALL 2 safe units,
        # rather than preserving the v1 10%-20% throttle reserve.
        signals = evaluate(cash=D("0"), safe=D("2"), equity_price=D("30"), starting=D("1000"))
        q = next(x for x in signals if x["kind"] == "QUARTERLY")
        safe_sales = sum(D(a["estimated_gbp"]) for a in q["actions"]
                         if a["symbol"] == "AGBP.L" and a["side"] == "SELL")
        equity_buys = sum(D(a["estimated_gbp"]) for a in q["actions"] if a["side"] == "BUY")
        self.assertEqual(safe_sales, D("200.00"))
        self.assertLessEqual(equity_buys, safe_sales)
        self.assertEqual(q["reset_reason"], "")
        self.assertIn("Your reserve is now empty", q["message"])

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

    @patch("tracker.market_signals.urlopen")
    def test_secondary_yahoo_chart_host_recovers_from_primary_outage(self, open_url):
        epoch = 1577923200
        content = json.dumps({"chart": {"result": [{
            "meta": {"currency": "GBP"},
            "timestamp": [epoch],
            "indicators": {"quote": [{"close": [50.5]}]},
        }]}}).encode("utf-8")
        response = MagicMock()
        response.status = 200
        response.read.return_value = content
        response.__enter__.return_value = response
        open_url.side_effect = [OSError("primary unavailable"), response]
        currency, rows = fetch_chart("ERNS.L", date(2020, 1, 1))
        self.assertEqual(open_url.call_count, 2)
        self.assertEqual(currency, "GBP")
        self.assertEqual(rows[0][1], D("50.50000000"))

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
        self.account = Account.objects.create(
            owner=self.owner, name="ISA monitoring", kind="ISA",
            country="GB", broker="TRADING212")
        self.preset = StrategyPreset.objects.get(slug="nasdaq-quarterly-target")
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=self.account, preset=self.preset,
            name="9Sig monitored", equity_symbol="LQQ3.L", safe_symbol="ERNS.L", market_data_enabled=True,
        )

    def test_only_owner_can_update_monitoring_settings_or_acknowledge(self):
        url = reverse("save_automation", args=[self.strategy.pk])
        self.client.force_login(self.other)
        response = self.client.post(url, {
            "equity_symbol": "CHANGED.L", "safe_symbol": "ERNS.L", "market_data_enabled": "on",
        })
        self.assertEqual(response.status_code, 404)
        self.strategy.refresh_from_db()
        self.assertEqual(self.strategy.equity_symbol, "LQQ3.L")
        self.client.force_login(self.owner)
        response = self.client.post(url, {
            "equity_symbol": " LQQ3.L ", "safe_symbol": " ERNS.L ",
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
            kind="BUY", occurred_on=start, symbol="ERNS.L", quantity=D("5"), unit_price=D("80"),
        ))
        from tracker.market_signals import quarter_end_before
        end = quarter_end_before(now)
        previous_end = quarter_end_before(end)
        # Use the supported UK ERNS line and the complete SPY rolling
        # eight-quarter reference; old AGBP/two-close fixtures were not
        # eligible to issue real-market 9Sig trading suggestions.
        from tracker.market_signals import quarter_number
        review_q = quarter_number(end)
        for offset in range(2, 9):
            year, q_index = divmod(review_q - offset, 4)
            month = (q_index + 1) * 3
            prior = date(year, month, monthrange(year, month)[1])
            MarketObservation.objects.create(
                symbol="SPY", as_of=prior, price=D("100"), currency="USD")
        MarketObservation.objects.create(
            symbol="SPY", as_of=previous_end, price=D("100"), currency="USD")
        MarketObservation.objects.create(
            symbol="SPY", as_of=end, price=D("65"), currency="USD")
        MarketObservation.objects.create(
            symbol="TQQQ", as_of=previous_end, price=D("100"), currency="USD")
        MarketObservation.objects.create(
            symbol="TQQQ", as_of=start, price=D("100"), currency="USD")
        MarketObservation.objects.create(
            symbol="TQQQ", as_of=end, price=D("65"), currency="USD")
        MarketObservation.objects.create(
            symbol="LQQ3.L", as_of=end, price=D("48"), currency="GBP")
        MarketObservation.objects.create(
            symbol="ERNS.L", as_of=end, price=D("81"), currency="GBP")
        reference = MarketObservation.objects.create(
            symbol="TQQQ", as_of=now, price=D("65"), currency="USD")
        spy = MarketObservation.objects.create(
            symbol="SPY", as_of=now, price=D("65"), currency="USD")
        equity = MarketObservation.objects.create(
            symbol="LQQ3.L", as_of=now, price=D("48"), currency="GBP")
        safe = MarketObservation.objects.create(
            symbol="ERNS.L", as_of=now, price=D("81"), currency="GBP")
        mapped = {"TQQQ": reference, "SPY": spy,
                  "LQQ3.L": equity, "ERNS.L": safe}
        from tracker.models import NotificationPreference
        NotificationPreference.objects.update_or_create(
            owner=self.owner,
            defaults={"email_enabled": True, "discord_enabled": False})
        with patch("tracker.market_signals.sync_ticker", side_effect=lambda symbol: mapped[symbol]), \
             patch("tracker.market_signals.dispatch") as dispatcher:
            first = review_strategy(self.strategy)
            second = review_strategy(self.strategy)
        self.assertEqual(first["status"], "ok")
        self.assertGreaterEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        # THIRTY_DOWN is urgent and immediate; QUARTERLY moves to the
        # scheduled digest so it must NOT trigger a second instant alert.
        self.assertEqual(first["created"], 2)
        self.assertEqual(dispatcher.call_count, 1)
        self.assertIn('30down:', dispatcher.call_args.args[1])
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
