"""Share splits and other October 2026 ledger and data fixes."""
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from tracker.models import LedgerEntry
from tracker.services import ledger_summary, validate_and_record
from tracker import test_v2_phase3 as phase3
from tracker.v2_ledger_core import effective_entries, replay


class ShareSplitTests(TestCase):
    # Borrow the ledger fixture's helpers without re-running its tests.
    _instance = phase3.LedgerVersioningTests._instance
    _entry = phase3.LedgerVersioningTests._entry
    _ledger = phase3.LedgerVersioningTests._ledger
    _payload = phase3.LedgerVersioningTests._payload

    def setUp(self):
        phase3.LedgerVersioningTests.setUp(self)
        self._entry(amount="1000")
        self._entry(kind="BUY", amount="0", symbol="LQQ3.L", qty="10", unit="50", offset=1)

    def test_a_split_multiplies_shares_and_keeps_cost_and_cash(self):
        from tracker import v2_dashboard as dash
        self._entry(kind="SPLIT", amount="0", symbol="LQQ3.L", qty="2", offset=2)
        summary = ledger_summary(self.instance)
        self.assertEqual(summary["positions"]["LQQ3.L"], D("20"))
        self.assertEqual(summary["cash"], D("500"))
        self.assertEqual(replay(effective_entries(self.instance))["positions"]["LQQ3.L"], D("20"))
        summary["marks"] = {"LQQ3.L": type("M", (), {"price": D("25"), "as_of": self.day})()}
        summary["value"] = D("1000")
        rows = dash.holdings_detail(self.instance, summary, {"LQQ3.L": "Growth"})
        self.assertEqual(rows[0]["pl"], D("0.00"))  # 20 × £25 = the £500 paid

    def test_a_consolidation_divides_shares(self):
        self._entry(kind="SPLIT", amount="0", symbol="LQQ3.L", qty="0.1", offset=2)
        self.assertEqual(ledger_summary(self.instance)["positions"]["LQQ3.L"], D("1"))

    def test_a_split_needs_a_held_fund_and_a_real_ratio(self):
        with self.assertRaises(ValidationError):
            self._entry(kind="SPLIT", amount="0", symbol="ERNS.L", qty="2", offset=2)
        with self.assertRaises(ValidationError):
            self._entry(kind="SPLIT", amount="0", symbol="LQQ3.L", qty="1", offset=2)

    def test_recorded_through_the_ledger_form_with_every_fund_offered(self):
        add = reverse("v2_ledger_add", args=[self.instance.pk])
        page = self.client.get(add)
        self.assertContains(page, 'value="SPLIT"')
        self.assertContains(page, 'value="ERNS.L"')
        self.client.post(add, self._payload(kind="SPLIT", symbol="LQQ3.L", ratio="3", amount="",
                                            occurred_on=(self.day + __import__("datetime").timedelta(days=2)).isoformat()))
        self.client.post(add, {"intent": "confirm"})
        self.assertEqual(ledger_summary(self.instance)["positions"]["LQQ3.L"], D("30"))
        ledger = self.client.get(self._ledger())
        self.assertContains(ledger, "Split ×3")

    def test_a_split_after_a_review_blocks_recording_its_old_quantities(self):
        from tracker.cockpit_execution import _external_after_signal
        from tracker.models import StrategySignal
        signal = StrategySignal.objects.create(strategy=self.instance, kind="QUARTERLY", as_of=self.day,
                                               signal_key="q", title="t", message="m", actions=[])
        self._entry(kind="SPLIT", amount="0", symbol="LQQ3.L", qty="2", offset=2)
        self.assertTrue(_external_after_signal(self.instance, signal, []))


class YahooDatesAndDividendsTests(TestCase):
    def test_bars_are_dated_in_exchange_time_and_dividends_are_kept(self):
        import io
        import json
        from datetime import date, datetime, timezone as tz
        from unittest.mock import patch
        from tracker.market_data import fetch_chart
        # GBP/USD daily bar for 1 Oct 2026, stamped at midnight London (23:00 UTC on 30 Sep).
        stamp = int(datetime(2026, 9, 30, 23, 0, tzinfo=tz.utc).timestamp())
        div_stamp = int(datetime(2026, 9, 30, 13, 30, tzinfo=tz.utc).timestamp())
        payload = {"chart": {"result": [{
            "meta": {"currency": "USD", "exchangeTimezoneName": "Europe/London"},
            "timestamp": [stamp], "indicators": {"quote": [{"close": [1.33]}]},
            "events": {"dividends": {str(div_stamp): {"amount": 0.42, "date": div_stamp}}},
        }]}}
        body = io.BytesIO(json.dumps(payload).encode())
        response = type("R", (), {"status": 200, "read": body.read,
                                  "__enter__": lambda self: self, "__exit__": lambda self, *a: None})()
        with patch("tracker.market_data.urlopen", return_value=response), \
                patch("tracker.market_data._unfinished_session_start", return_value=float("inf")):
            chart = fetch_chart("BIL", date(2026, 9, 1))
        self.assertEqual(chart.series[0][0], date(2026, 10, 1))
        self.assertEqual(chart.dividends, {date(2026, 9, 30): D("0.42")})


class DualMomentumIncomeTests(TestCase):
    def setUp(self):
        from datetime import date
        from tracker.models import DividendEvent, MarketObservation
        self.begin_day, self.end_day = date(2025, 9, 30), date(2026, 9, 30)
        for day, amount in ((date(2026, 3, 2), "2.00"), (date(2026, 9, 1), "2.00"),
                            (date(2025, 9, 1), "9.99")):  # the last is before the window
            DividendEvent.objects.create(symbol="BIL", ex_date=day, amount=D(amount), currency="USD")
            MarketObservation.objects.create(
                symbol="BIL", as_of=day, price=D("100"), currency="USD", source="yahoo",
                raw_unit_verified=True, raw_quote_unit="USD", raw_close=D("100"))

    def quotes(self):
        from tracker.strategies.dual_momentum import v1
        return (v1.Quote("BIL", D("91.50"), self.begin_day), v1.Quote("BIL", D("91.60"), self.end_day))

    def test_income_is_reinvested_at_each_ex_date_close(self):
        from tracker.strategies.dual_momentum import v1
        from tracker.strategies.dual_momentum.plugin import _with_income
        begin, end = self.quotes()
        total = v1.Lookback(begin, _with_income(end, begin, "USD")).total_return
        self.assertAlmostEqual(float(total), 91.60 * 1.02 * 1.02 / 91.50 - 1, places=6)

    def test_a_hole_in_the_payment_history_holds_the_decision(self):
        from tracker.models import DividendEvent
        from tracker.strategies.dual_momentum.plugin import _with_income
        begin, end = self.quotes()
        DividendEvent.objects.filter(ex_date__year=2026, ex_date__month=9).delete()
        self.assertIsNone(_with_income(end, begin, "USD"))  # last payment too far from the end

    def test_a_payment_without_a_verified_close_holds_the_decision(self):
        from tracker.models import MarketObservation
        from tracker.strategies.dual_momentum.plugin import _with_income
        begin, end = self.quotes()
        MarketObservation.objects.filter(as_of=self.end_day.replace(month=9, day=1)).update(raw_unit_verified=False)
        self.assertIsNone(_with_income(end, begin, "USD"))


class StudyNextDayFillTests(TestCase):
    def test_next_day_fills_use_the_following_close(self):
        from datetime import timedelta
        from django.core.cache import cache
        from tracker.strategies.registry import module_for_key
        from tracker.test_study import TODAY, seed
        from tracker.test_study_fixed import seed_all_weather
        cache.clear()
        start = TODAY - timedelta(days=800)
        seed_all_weather(start, start, gold=lambda d, i: 50 + (i % 7) * 3.0)
        study = module_for_key("all-weather").study["GBP"]
        data = study.load_data()
        same = study.run(data, "1y", 1000, 0)
        later = study.run(data, "1y", 1000, 0, "next")
        self.assertEqual(len(same["series"]["strategy"]["values"]), len(later["series"]["strategy"]["values"]))
        self.assertNotEqual(same["series"]["strategy"]["final"], later["series"]["strategy"]["final"])
        self.assertGreater(later["series"]["strategy"]["trades"], 0)

    def test_the_study_page_offers_the_choice(self):
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.create_user("study-fill", password="a-long-test-password-1")
        self.client.force_login(user)
        self.client.post(reverse("v2_add_strategy"), {
            "strategy": "all-weather", "platform": "TRADING212", "account_type": "ISA", "ack": "yes",
            "mode": "fresh", "name": "AW", "start": "2026-10-01", "amount": "1000", "deposited": "yes"})
        from tracker.models import StrategyInstance
        strategy = StrategyInstance.objects.get(owner=user)
        page = self.client.get(reverse("v2_instance_page", args=[strategy.pk, "study"]) + "?fill=next")
        self.assertEqual(page.status_code, 200)


class SmallFixesTests(TestCase):
    def test_isa_limit_status_counts_down_to_the_new_tax_year(self):
        from datetime import date
        from tracker.core.isa_allowance import limit_check_status
        now = limit_check_status(date(2026, 10, 22))
        self.assertTrue(now["current"])
        self.assertEqual(now["expires"], date(2027, 4, 6))
        self.assertFalse(limit_check_status(date(2027, 4, 6))["current"])
