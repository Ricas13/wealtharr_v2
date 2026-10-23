"""Nasdaq Trend + DCA: signal hysteresis, guards, monthly proceeds and catalogue."""
from datetime import date, timedelta
from decimal import Decimal as D

from django.test import SimpleTestCase, TestCase

from tracker.strategies.fixed_weights import Quote
from tracker.strategies.nasdaq_trend_dca import v1
from tracker.strategies.registry import describe_strategy, module_for_key
from tracker.core.catalogue import selection_available, onboarding_manifest


def weekdays(count, first=date(2023, 1, 2)):
    days = []
    d = first
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


class NasdaqTrendRuleTests(SimpleTestCase):
    def test_no_history_or_undecided_band_refuses_to_guess(self):
        days = weekdays(200)
        flat = [(d, 100) for d in days]
        self.assertIsNone(v1.trend_at(flat[:199], flat[:199], days[-1]))
        self.assertIsNone(v1.trend_at(flat, flat, days[-1]))

    def test_hysteresis_and_qqq_guard_precedence(self):
        days = weekdays(240)
        spy = [(d, 100 if i < 200 else 110) for i, d in enumerate(days)]
        qqq = [(d, 100 if i < 200 else 150) for i, d in enumerate(days)]
        signals = v1.trend_series(spy, qqq)
        self.assertEqual(signals[days[201]].mode, "cash")
        # QQQ rising only 35% above its average reduces to unleveraged.
        qqq = [(d, 100 if i < 200 else 135) for i, d in enumerate(days)]
        signals = v1.trend_series(spy, qqq)
        self.assertEqual(signals[days[201]].mode, "unleveraged")
        # Return to the middle of the SPY band: stay in the earlier regime.
        spy[-1] = (days[-1], 101)
        self.assertEqual(v1.trend_at(spy, qqq, days[-1]).regime, "on")

    def test_bear_exit_starts_dca_after_first_month(self):
        days = weekdays(260)
        spy = [(d, 100 if i < 200 else 80) for i, d in enumerate(days)]
        qqq = [(d, 100) for d in days]
        state = v1.trend_at(spy, qqq, days[-1])
        self.assertEqual(state.mode, "dca")
        self.assertGreaterEqual(state.months_elapsed, 1)
        self.assertEqual(state.exit_on, days[200])

    def test_end_of_month_anniversary(self):
        self.assertEqual(v1.elapsed_months(date(2026, 1, 31), date(2026, 2, 28)), 1)

    def test_proceeds_dca_and_reentry_are_manual_actions(self):
        day = date(2026, 9, 30)
        funds = {"leveraged": "LQQ3.L", "unleveraged": "EQQQ.L"}
        quotes = {s: Quote(s, D("10"), day) for s in funds.values()}
        off = v1.TrendSignal(day, "dca", "off", date(2026, 8, 20),
                             1, D("0.9"), D("1.0"))
        result = v1.evaluate(funds=funds, positions={"LQQ3.L": D("12")},
                             cash=D("120"), quotes=quotes, signal=off)
        self.assertEqual([t.side for t in result.trades], ["SELL"])
        self.assertEqual(result.trades[0].symbol, "LQQ3.L")
        # The next confirmed review spends exactly 1/12 of cash proceeds.
        dca = v1.evaluate(funds=funds, positions={}, cash=D("240"),
                          quotes=quotes, signal=off)
        self.assertEqual([t.side for t in dca.trades], ["BUY"])
        self.assertEqual(dca.trades[0].symbol, "EQQQ.L")
        self.assertEqual(dca.trades[0].estimated_gbp, D("20.00"))
        restricted = v1.evaluate(
            funds=funds, positions={}, cash=D("240"), quotes=quotes,
            signal=off, dca_budget=D("120"))
        self.assertEqual(restricted.trades[0].estimated_gbp, D("10.00"))
        self.assertEqual(len(restricted.trades), 1)
        # The same monthly purchase, once confirmed, must not be repeated.
        hold = v1.evaluate(funds=funds, positions={"EQQQ.L": D("2")},
                           cash=D("220"), quotes=quotes, signal=off, completed_dca=1)
        self.assertEqual(hold.trades, ())
        bull = v1.TrendSignal(day, "leveraged", "on", None, 0,
                              D("1.1"), D("1.02"))
        back = v1.evaluate(funds=funds, positions={"EQQQ.L": D("2")},
                           cash=D("220"), quotes=quotes, signal=bull)
        self.assertEqual([t.side for t in back.trades], ["SELL", "BUY"])
        self.assertEqual(back.trades[-1].symbol, "LQQ3.L")

    def test_negative_cash_and_unknown_holding_fail_closed(self):
        funds = {"leveraged": "LQQ3.L", "unleveraged": "EQQQ.L"}
        day = date(2026, 9, 30)
        quotes = {s: Quote(s, D("10"), day) for s in funds.values()}
        sig = v1.TrendSignal(day, "leveraged", "on", None, 0,
                             D("1.1"), D("1.0"))
        for positions, cash in [({}, D("-1")), ({"ZZZ": D("1")}, D("100"))]:
            result = v1.evaluate(funds=funds, positions=positions,
                                 cash=cash, quotes=quotes, signal=sig)
            self.assertEqual(result.status, "cannot_compute")
            self.assertEqual(result.trades, ())

    def test_released_alongside_ninesig_with_real_uk_lines(self):
        self.assertIsNotNone(describe_strategy("nasdaq-quarterly-target"))
        self.assertIsNotNone(module_for_key("nasdaq-trend-dca"))
        self.assertTrue(selection_available("nasdaq-trend-dca", "TRADING212", "ISA"))
        manifest = onboarding_manifest("nasdaq-trend-dca", "TRADING212", "ISA")
        self.assertEqual(manifest["roles"]["leveraged"]["yahoo_ticker"], "LQQ3.L")
        self.assertEqual(manifest["roles"]["unleveraged"]["yahoo_ticker"], "EQQQ.L")
        self.assertEqual(manifest["roles"]["unleveraged"]["raw_quote_unit"], "GBX")


class NasdaqTrendStudyTests(SimpleTestCase):
    def test_study_uses_the_daily_rules_and_owns_no_ledger(self):
        from tracker.study_fixed import FixedData
        from tracker.strategies.nasdaq_trend_dca.study import TrendStudy
        days = weekdays(270)
        data = FixedData(
            days=days,
            prices={"leveraged": [10.0 + 0.01 * i for i in range(270)],
                    "unleveraged": [10.0 + 0.005 * i for i in range(270)]},
            spy=[10.0] * 270, vti=[10.0] * 270)
        data.trend = {}
        for i in range(200, 270):
            if i < 225:
                data.trend[days[i]] = v1.TrendSignal(
                    days[i], "leveraged", "on", None, 0, D("1.1"), D("1.01"))
            else:
                exit_on = days[225]
                data.trend[days[i]] = v1.TrendSignal(
                    days[i], "dca", "off", exit_on,
                    v1.elapsed_months(exit_on, days[i]), D("0.90"), D("0.95"))
        engine = TrendStudy(title="Nasdaq trend", sources={})
        same = engine.run(data, "max", 1000, 0, "close")
        next_day = engine.run(data, "max", 1000, 0, "next")
        self.assertIsNotNone(same["series"]["strategy"])
        self.assertIsNotNone(next_day["series"]["strategy"])
        self.assertGreaterEqual(same["series"]["strategy"]["trades"], 2)
        self.assertEqual(same["series"]["strategy"]["put_in"], 1000)
        self.assertEqual(same["start"], days[0])


class NasdaqCashRegimeIntegrationTests(TestCase):
    def test_cash_only_is_not_an_unfunded_strategy(self):
        from unittest.mock import patch
        from django.contrib.auth import get_user_model
        from django.utils import timezone
        from tracker.models import Account, StrategyPreset, StrategyInstance, PriceMark, MarketObservation
        from tracker.signal_status import _price_status
        from tracker.v2_today import _opening_allocation_available
        from tracker.strategies.registry import module_for

        user = get_user_model().objects.create_user("trend-cash-user", password="example")
        account = Account.objects.create(owner=user, name="Trend Cash ISA", kind="ISA",
                                         broker="TRADING212")
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-trend-dca", defaults={"name": "Trend", "summary": "",
                                             "risk": "HIGH"})
        instance = StrategyInstance.objects.create(
            owner=user, account=account, preset=preset,
            name="Trend cash", strategy_key="nasdaq-trend-dca",
            market_data_enabled=True, tracking_only=False,
            resolved_instruments={"roles": {
                "leveraged": {"yahoo_ticker": "LQQ3.L"},
                "unleveraged": {"yahoo_ticker": "EQQQ.L"}}})
        day = timezone.localdate() - timedelta(days=1)
        marks = {}
        for symbol in ("LQQ3.L", "EQQQ.L"):
            MarketObservation.objects.create(symbol=symbol, as_of=day,
                                             currency="GBP", price=D("10"))
            marks[symbol] = PriceMark.objects.create(
                strategy=instance, symbol=symbol, as_of=day, source="yahoo",
                price=D("10"))
        report = {"cash": D("100"), "positions": {}, "missing": [], "marks": marks}
        with patch("tracker.signal_status.for_strategy", return_value=object()):
            ready, reason, _ = _price_status(instance, report)
        self.assertTrue(ready, reason)
        plugin = module_for(instance)
        self.assertTrue(plugin.allows_cash_only)
        from types import SimpleNamespace
        cash_mode = SimpleNamespace(
            allows_cash_only=True,
            current_weights=lambda strategy: {"LQQ3.L": D("0"), "EQQQ.L": D("0")})
        self.assertFalse(_opening_allocation_available(instance, cash_mode))


class NasdaqDailyOwnerJourneyTests(TestCase):
    def test_add_strategy_review_is_deduplicated_and_today_shows_trade(self):
        from unittest.mock import patch
        from django.contrib.auth import get_user_model
        from django.urls import reverse
        from django.utils import timezone
        from tracker.core.catalogue import onboarding_manifest, CATALOGUE_VERSION
        from tracker.models import (
            Account, LedgerEntry, MarketObservation, StrategyInstance,
            StrategyPreset, StrategySignal)
        from tracker.strategies.nasdaq_trend_dca.plugin import review

        owner = get_user_model().objects.create_user("trend-journey", password="test-strong-1")
        account = Account.objects.create(
            owner=owner, name="Trend Journey ISA", kind="ISA", broker="TRADING212")
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-trend-dca", defaults={"name": "3x Nasdaq Trend + DCA",
                                             "summary": "", "risk": "HIGH"})
        # Historical SPY/QQQ observations ending on the last weekday before today.
        today = timezone.localdate()
        dates = sorted(today - timedelta(days=i) for i in range(1, 340)
                       if (today - timedelta(days=i)).weekday() < 5)[-220:]
        strategy = StrategyInstance.objects.create(
            owner=owner, account=account, preset=preset, name="Trend Journey",
            strategy_key="nasdaq-trend-dca", started_on=dates[-10],
            platform_code="TRADING212", account_type_code="ISA",
            strategy_version=v1.VERSION, mapping_version=CATALOGUE_VERSION,
            resolved_instruments=onboarding_manifest(
                "nasdaq-trend-dca", "TRADING212", "ISA"),
            tracking_only=False, market_data_enabled=True)
        LedgerEntry.objects.create(strategy=strategy, kind=LedgerEntry.DEPOSIT,
                                   amount=D("1000"), occurred_on=dates[-2])
        for i, d in enumerate(dates):
            for symbol, value in (("SPY", D("110") if i >= 200 else D("100")),
                                  ("QQQ", D("100"))):
                MarketObservation.objects.create(
                    symbol=symbol, as_of=d, price=value, currency="USD",
                    source="yahoo", raw_quote_unit="USD", raw_unit_verified=True,
                    raw_close=value)
        for symbol in ("LQQ3.L", "EQQQ.L"):
            MarketObservation.objects.create(
                symbol=symbol, as_of=dates[-1], price=D("10"),
                currency="GBP", source="yahoo", raw_quote_unit="GBp",
                raw_unit_verified=True, raw_close=D("1000"))

        def cached_sync(symbol, **kwargs):
            return MarketObservation.objects.filter(symbol=symbol).order_by("-as_of").first()

        with patch("tracker.market_data.sync_ticker", side_effect=cached_sync):
            self.assertEqual(review(strategy)["status"], "ok")
            self.assertEqual(review(strategy)["status"], "ok")
        signals = StrategySignal.objects.filter(strategy=strategy)
        self.assertEqual(signals.count(), 1)
        signal = signals.get()
        self.assertEqual(signal.strategy_version, v1.VERSION)
        self.assertEqual(signal.actions[0]["side"], "BUY")
        self.assertEqual(signal.actions[0]["symbol"], "LQQ3.L")
        self.client.force_login(owner)
        page = self.client.get(reverse("v2_instance_page",
                                       args=(strategy.pk, "today")))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Review the verified daily proposal")
