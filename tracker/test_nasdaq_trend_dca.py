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


def piecewise(days, points):
    """Closes that hold each value from its index until the next point."""
    out, value, queue = [], None, dict(points)
    for i, day in enumerate(days):
        value = queue.get(i, value)
        out.append((day, value))
    return out


FUNDS = {"leveraged": "LQQ3.L", "unleveraged": "EQQQ.L"}
DAY = date(2026, 9, 30)
QUOTES = {s: Quote(s, D("10"), DAY) for s in FUNDS.values()}


def signal(mode, regime="on", guard="none", exit_on=None, as_of=DAY):
    return v1.TrendSignal(as_of, mode, regime, guard, exit_on, D("1.1"), D("1.0"))


class NasdaqTrendRuleTests(SimpleTestCase):
    def test_no_history_or_undecided_band_refuses_to_guess(self):
        days = weekdays(200)
        flat = [(d, 100) for d in days]
        self.assertIsNone(v1.trend_at(flat[:199], flat[:199], days[-1]))
        self.assertIsNone(v1.trend_at(flat, flat, days[-1]))

    def test_spy_bands_have_hysteresis(self):
        days = weekdays(260)
        spy = piecewise(days, [(0, 100), (200, 110), (230, 101), (250, 90)])
        qqq = [(d, 100) for d in days]
        series = v1.trend_series(spy, qqq)
        self.assertEqual(series[days[200]].mode, "leveraged")
        # Inside the band (97%-104% of the average) the earlier state holds.
        self.assertEqual(series[days[240]].regime, "on")
        self.assertEqual(series[days[255]].mode, "dca")
        self.assertIsNotNone(series[days[255]].exit_on)

    def test_qqq_guard_latches_until_spy_exits_and_re_enters(self):
        days = weekdays(420)
        spy = piecewise(days, [(0, 100), (200, 110), (300, 70), (330, 140)])
        qqq = piecewise(days, [(0, 100), (200, 100), (210, 135), (215, 100),
                               (230, 150), (235, 100)])
        series = v1.trend_series(spy, qqq)
        self.assertEqual(series[days[205]].mode, "leveraged")
        self.assertEqual(series[days[211]].mode, "unleveraged")
        # QQQ cooled off, but the guard stays on (not re-leveraged).
        self.assertEqual(series[days[225]].mode, "unleveraged")
        self.assertEqual(series[days[231]].mode, "cash")
        self.assertEqual(series[days[260]].mode, "cash")
        self.assertEqual(series[days[320]].mode, "dca")
        self.assertEqual(series[days[320]].guard, "none")
        self.assertEqual(series[days[400]].mode, "leveraged")

    def test_a_signal_never_uses_later_closes(self):
        days = weekdays(300)
        spy = piecewise(days, [(0, 100), (200, 110), (260, 60)])
        qqq = [(d, 100) for d in days]
        full = v1.trend_series(spy, qqq)
        for cut in (210, 259, 280):
            self.assertEqual(v1.trend_at(spy, qqq, days[cut]), full[days[cut]])

    def test_end_of_month_anniversary(self):
        self.assertEqual(v1.elapsed_months(date(2026, 1, 31), date(2026, 2, 28)), 1)
        self.assertEqual(v1.elapsed_months(date(2026, 1, 15), date(2026, 2, 14)), 0)
        self.assertEqual(v1.elapsed_months(date(2024, 1, 15), date(2026, 1, 15)), 12)

    def test_instalment_schedule_follows_the_money_not_the_calendar_alone(self):
        exit_on = date(2026, 1, 10)
        inflows = [(exit_on, D("1200"))]
        self.assertEqual(v1.dca_target(inflows, exit_on, date(2026, 2, 9)), D("0"))
        self.assertEqual(v1.dca_target(inflows, exit_on, date(2026, 2, 10)), D("100"))
        self.assertEqual(v1.dca_target(inflows, exit_on, date(2026, 6, 10)), D("500"))
        self.assertEqual(v1.dca_target(inflows, exit_on, date(2027, 6, 1)), D("1200"))
        # New money has its own twelve-month schedule from its own date.
        more = inflows + [(date(2026, 3, 10), D("600"))]
        self.assertEqual(v1.dca_target(more, exit_on, date(2026, 4, 10)), D("300") + D("50"))
        # Money that arrived before the exit is released from the exit.
        before = [(date(2025, 6, 1), D("1200"))]
        self.assertEqual(v1.dca_target(before, exit_on, date(2026, 2, 10)), D("100"))

    def test_exit_sells_everything_then_instalments_buy_only_after_sales_recorded(self):
        exit_on = date(2026, 8, 20)
        off = signal("dca", "off", exit_on=exit_on)
        result = v1.evaluate(funds=FUNDS, positions={"LQQ3.L": D("12"), "EQQQ.L": D("3")},
                             cash=D("5"), quotes=QUOTES, signal=off,
                             inflows=((exit_on, D("5")),))
        self.assertEqual([(t.side, t.symbol) for t in result.trades],
                         [("SELL", "LQQ3.L"), ("SELL", "EQQQ.L")])
        # Sales recorded: the 1/12 instalment is due a month after the exit.
        inflows = ((exit_on, D("5")), (date(2026, 8, 21), D("1195")))
        dca = v1.evaluate(funds=FUNDS, positions={}, cash=D("1200"), quotes=QUOTES,
                          signal=off, inflows=inflows)
        self.assertEqual([(t.side, t.symbol) for t in dca.trades], [("BUY", "EQQQ.L")])
        self.assertEqual(dca.trades[0].estimated_gbp, D("100.00"))

    def test_cash_taken_out_slows_the_remaining_schedule(self):
        exit_on = date(2026, 1, 10)
        flows = [(exit_on, D("1200")), (date(2026, 4, 10), D("-600"))]
        # Month 3: nothing withdrawn yet. Month 5: 500 less 2 months of the 600 withdrawn.
        self.assertEqual(v1.dca_target(flows, exit_on, date(2026, 4, 10)), D("300"))
        self.assertEqual(v1.dca_target(flows, exit_on, date(2026, 6, 10)), D("400"))
        self.assertEqual(v1.dca_target(flows, exit_on, date(2027, 6, 1)), D("600"))

    def test_instalments_are_not_repeated_and_later_buys_are_not_sold(self):
        exit_on = date(2026, 8, 20)
        off = signal("dca", "off", exit_on=exit_on)
        inflows = ((date(2026, 8, 21), D("1200")),)
        done = v1.evaluate(funds=FUNDS, positions={"EQQQ.L": D("10")}, cash=D("1100"),
                           quotes=QUOTES, signal=off, inflows=inflows,
                           dca_spent=D("100"))
        self.assertEqual(done.trades, ())
        self.assertEqual(done.status, "ok")

    def test_after_twelve_months_everything_is_invested(self):
        exit_on = date(2025, 6, 2)
        off = signal("dca", "off", exit_on=exit_on, as_of=date(2026, 9, 30))
        result = v1.evaluate(funds=FUNDS, positions={"EQQQ.L": D("110")}, cash=D("100"),
                             quotes=QUOTES, signal=off,
                             inflows=((exit_on, D("1200")),), dca_spent=D("1100"))
        self.assertEqual(result.trades[0].estimated_gbp, D("100.00"))

    def test_re_entry_moves_everything_back_to_three_times(self):
        back = v1.evaluate(funds=FUNDS, positions={"EQQQ.L": D("2")}, cash=D("220"),
                           quotes=QUOTES, signal=signal("leveraged"))
        self.assertEqual([t.side for t in back.trades], ["SELL", "BUY"])
        self.assertEqual(back.trades[-1].symbol, "LQQ3.L")

    def test_guard_modes_hold_the_matching_fund_or_cash(self):
        mid = v1.evaluate(funds=FUNDS, positions={"LQQ3.L": D("5")}, cash=D("0"),
                          quotes=QUOTES, signal=signal("unleveraged", guard="unleveraged"))
        self.assertEqual([(t.side, t.symbol) for t in mid.trades],
                         [("SELL", "LQQ3.L"), ("BUY", "EQQQ.L")])
        cash = v1.evaluate(funds=FUNDS, positions={"EQQQ.L": D("5")}, cash=D("0"),
                           quotes=QUOTES, signal=signal("cash", guard="cash"))
        self.assertEqual([(t.side, t.symbol) for t in cash.trades], [("SELL", "EQQQ.L")])

    def test_negative_cash_and_unknown_holding_fail_closed(self):
        for positions, cash in [({}, D("-1")), ({"ZZZ": D("1")}, D("100"))]:
            result = v1.evaluate(funds=FUNDS, positions=positions, cash=cash,
                                 quotes=QUOTES, signal=signal("leveraged"))
            self.assertEqual(result.status, "cannot_compute")
            self.assertEqual(result.trades, ())

    def test_missing_quote_or_history_fails_closed(self):
        self.assertEqual(v1.evaluate(funds=FUNDS, positions={}, cash=D("1"), quotes={},
                                     signal=signal("leveraged")).status, "cannot_compute")
        self.assertEqual(v1.evaluate(funds=FUNDS, positions={}, cash=D("1"),
                                     quotes=QUOTES, signal=None).status, "cannot_compute")


class NasdaqLedgerScheduleTests(SimpleTestCase):
    def test_flows_and_spend_come_from_the_ledger_alone(self):
        from types import SimpleNamespace as E
        from tracker.strategies.nasdaq_trend_dca.plugin import _cash_flows
        exit_on = date(2026, 8, 20)
        entries = [
            E(kind="DEPOSIT", occurred_on=date(2026, 1, 5), amount=D("500"), symbol="",
              quantity=D("0"), unit_price=D("0"), fee=D("0")),
            E(kind="SELL", occurred_on=date(2026, 8, 21), amount=D("0"), symbol="LQQ3.L",
              quantity=D("10"), unit_price=D("20"), fee=D("1")),
            E(kind="DEPOSIT", occurred_on=date(2026, 9, 1), amount=D("100"), symbol="",
              quantity=D("0"), unit_price=D("0"), fee=D("0")),
            E(kind="BUY", occurred_on=date(2026, 9, 22), amount=D("0"), symbol="EQQQ.L",
              quantity=D("2"), unit_price=D("30"), fee=D("0")),
        ]
        off = signal("dca", "off", exit_on=exit_on)
        inflows, spent = _cash_flows(None, off, FUNDS, entries)
        self.assertEqual(inflows, ((exit_on, D("500")), (date(2026, 8, 21), D("199")),
                                   (date(2026, 9, 1), D("100"))))
        self.assertEqual(spent, D("60"))
        more = entries + [
            E(kind="WITHDRAW", occurred_on=date(2026, 9, 10), amount=D("50"), symbol="",
              quantity=D("0"), unit_price=D("0"), fee=D("0")),
            E(kind="FEE", occurred_on=date(2026, 9, 11), amount=D("5"), symbol="",
              quantity=D("0"), unit_price=D("0"), fee=D("0"))]
        flows, _ = _cash_flows(None, off, FUNDS, more)
        self.assertEqual(flows[-2:], ((date(2026, 9, 10), D("-50")), (date(2026, 9, 11), D("-5"))))
        self.assertEqual(_cash_flows(None, signal("leveraged"), FUNDS, entries), ((), D("0")))

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
                    days[i], "leveraged", "on", "none", None, D("1.1"), D("1.01"))
            else:
                exit_on = days[225]
                data.trend[days[i]] = v1.TrendSignal(
                    days[i], "dca", "off", "none", exit_on, D("0.90"), D("0.95"))
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
            MarketObservation.objects.create(source="yahoo", raw_unit_verified=True, symbol=symbol, as_of=day,
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
        self.assertContains(page, "Daily rules output")


class NasdaqCurrentReviewWindowTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from tracker.core.catalogue import onboarding_manifest, CATALOGUE_VERSION
        from tracker.models import (
            Account, LedgerEntry, MarketObservation, StrategyInstance, StrategyPreset)
        self.MarketObservation = MarketObservation
        owner = get_user_model().objects.create_user("trend-window", password="test-strong-1")
        account = Account.objects.create(
            owner=owner, name="Trend Window ISA", kind="ISA", broker="TRADING212")
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-trend-dca", defaults={"name": "3x Nasdaq Trend + DCA",
                                             "summary": "", "risk": "HIGH"})
        from django.utils import timezone
        today = timezone.localdate()
        self.today = today
        self.dates = sorted(today - timedelta(days=i) for i in range(1, 340)
                            if (today - timedelta(days=i)).weekday() < 5)[-220:]
        self.strategy = StrategyInstance.objects.create(
            owner=owner, account=account, preset=preset, name="Trend Window",
            strategy_key="nasdaq-trend-dca", started_on=self.dates[-10],
            platform_code="TRADING212", account_type_code="ISA",
            strategy_version=v1.VERSION, mapping_version=CATALOGUE_VERSION,
            resolved_instruments=onboarding_manifest("nasdaq-trend-dca", "TRADING212", "ISA"),
            tracking_only=False, market_data_enabled=True)
        LedgerEntry.objects.create(strategy=self.strategy, kind=LedgerEntry.DEPOSIT,
                                   amount=D("1000"), occurred_on=self.dates[-3])
        for i, d in enumerate(self.dates[:-1]):
            self.add_session(d, D("110") if i >= 200 else D("100"))

    def add_session(self, day, spy, fund=D("10")):
        for symbol, value, currency in (("SPY", spy, "USD"), ("QQQ", D("100"), "USD"),
                                        ("LQQ3.L", fund, "GBP"), ("EQQQ.L", fund, "GBP")):
            self.MarketObservation.objects.create(
                symbol=symbol, as_of=day, price=value, currency=currency, source="yahoo",
                raw_quote_unit="GBp" if currency == "GBP" else "USD",
                raw_unit_verified=True,
                raw_close=value * 100 if currency == "GBP" else value)

    def run_review(self):
        from unittest.mock import patch
        from tracker.strategies.nasdaq_trend_dca.plugin import review

        def cached(symbol, **kwargs):
            return self.MarketObservation.objects.filter(symbol=symbol).order_by("-as_of").first()
        with patch("tracker.market_data.sync_ticker", side_effect=cached):
            return review(self.strategy)

    def test_pending_instruction_is_kept_not_reissued_and_stays_current(self):
        from tracker.models import StrategySignal
        from tracker.strategies.registry import is_current_review
        self.assertEqual(self.run_review()["status"], "ok")
        first = StrategySignal.objects.get(strategy=self.strategy)
        self.assertEqual(first.as_of, self.dates[-2])
        # The next session's close arrives before the owner recorded any fill.
        self.add_session(self.dates[-1], D("110"))
        self.assertEqual(self.run_review()["status"], "ok")
        self.assertEqual(StrategySignal.objects.filter(strategy=self.strategy).count(), 1)
        first.refresh_from_db()
        self.assertEqual(first.review_outcome, "")
        # A daily plug-in's week-old window; a quarterly one's only last review.
        self.assertTrue(is_current_review(self.strategy, first.as_of, self.today))
        self.assertFalse(is_current_review(self.strategy, first.as_of - timedelta(days=30), self.today))

    def test_fills_can_still_be_recorded_after_the_next_close_lands(self):
        from django.core.exceptions import ValidationError
        from tracker.cockpit_execution import _check_current
        from tracker.models import StrategySignal
        self.run_review()
        signal = StrategySignal.objects.get(strategy=self.strategy)
        self.add_session(self.dates[-1], D("110"))
        try:
            _check_current(self.strategy, signal)
        except ValidationError as exc:  # pragma: no cover - failure path
            self.fail("current daily review rejected: %s" % exc)

    def test_changed_instruction_supersedes_the_unfilled_proposal(self):
        from tracker.models import StrategySignal
        self.run_review()
        old = StrategySignal.objects.get(strategy=self.strategy)
        # SPY collapses: the pending "buy 3x" is no longer a valid instruction.
        self.add_session(self.dates[-1], D("40"))
        self.assertEqual(self.run_review()["status"], "ok")
        old.refresh_from_db()
        self.assertEqual(old.review_outcome, "NOT_APPLICABLE")

    def test_a_large_price_move_reissues_the_instruction_with_fresh_share_counts(self):
        from tracker.models import StrategySignal
        self.run_review()
        old = StrategySignal.objects.get(strategy=self.strategy)
        self.add_session(self.dates[-1], D("110"), fund=D("14"))
        self.run_review()
        old.refresh_from_db()
        self.assertEqual(old.review_outcome, "NOT_APPLICABLE")
        new = StrategySignal.objects.exclude(pk=old.pk).get(strategy=self.strategy)
        self.assertNotEqual(new.actions[0]["quantity"], old.actions[0]["quantity"])

    def test_owner_is_told_when_an_alerted_instruction_is_withdrawn(self):
        from unittest.mock import patch
        from tracker.cockpit_notifications import send_due_cockpit_notices
        from tracker.models import NotificationDelivery, StrategySignal
        self.run_review()
        old = StrategySignal.objects.get(strategy=self.strategy)
        self.add_session(self.dates[-1], D("40"))  # rules no longer say buy 3x
        self.run_review()
        old.refresh_from_db()
        self.assertEqual(old.review_outcome, "NOT_APPLICABLE")
        calls = []
        with patch("tracker.cockpit_notifications._send",
                   side_effect=lambda *a, **k: calls.append(a[3]) or 1):
            send_due_cockpit_notices(self.today)
            self.assertFalse([c for c in calls if c.startswith("withdrawn:")])  # never alerted
            NotificationDelivery.objects.create(
                owner=self.strategy.owner, strategy=self.strategy, channel="email",
                category="quarterly", status="SENT",
                event_key="quarterly-digest:%s:%s" % (self.strategy.pk, old.signal_key))
            send_due_cockpit_notices(self.today)
        self.assertEqual([c for c in calls if c.startswith("withdrawn:")],
                         ["withdrawn:%s:%s" % (self.strategy.pk, old.pk)])
