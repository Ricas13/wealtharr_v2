"""Hypothetical Study tab: series building, rules replay, downloads and owner scope."""
from datetime import date, timedelta
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker import study
from tracker.models import (
    Account, LedgerEntry, MarketObservation, StrategyInstance, StrategyPreset, StudyPrice,
)
from tracker.study_data import sync_study_prices

TODAY = timezone.localdate()


def weekdays(start, end):
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def seed(symbol, start, price, currency="USD", end=TODAY):
    """price(day, i) -> value; stored as both close and adjusted close."""
    StudyPrice.objects.bulk_create([
        StudyPrice(symbol=symbol, as_of=day, close=D(str(round(price(day, i), 6))),
                   adj_close=D(str(round(price(day, i), 6))), currency=currency)
        for i, day in enumerate(weekdays(start, end))
    ])


class SeriesTests(TestCase):
    def setUp(self):
        cache.clear()
        start = TODAY - timedelta(days=400)
        self.start = start
        seed("QQQ", start, lambda d, i: 100 * (1.001 ** i))
        seed("SPY", start, lambda d, i: 200.0)
        seed("VTI", start + timedelta(days=100), lambda d, i: 50.0)
        seed("GBPUSD=X", start, lambda d, i: 1.25)

    def test_rebuilt_growth_is_three_times_daily_qqq_less_charge(self):
        data = study.load_data()
        daily = 3 * 0.001 - study.GROWTH_CHARGE / 252
        self.assertAlmostEqual(data.growth[1] / data.growth[0], 1 + daily, places=9)
        self.assertIsNone(data.growth_real_from)
        # No ERNS yet: the safe sleeve is cash at 0%.
        self.assertTrue(all(v == data.safe[0] for v in data.safe))
        # US funds are converted to pounds.
        self.assertAlmostEqual(data.spy[0], 200 / 1.25)
        # VTI is missing before its first price, then forward-filled.
        self.assertIsNone(data.vti[0])
        self.assertAlmostEqual(data.vti[-1], 50 / 1.25)

    def test_real_fund_is_spliced_on_without_a_jump(self):
        launch = TODAY - timedelta(days=200)
        seed("LQQ3.L", launch, lambda d, i: 30 + i * 0.01, currency="GBP")
        data = study.load_data()
        k = data.days.index(data.growth_real_from)
        self.assertGreaterEqual(data.growth_real_from, launch)
        self.assertAlmostEqual(data.growth[k], 30.0, places=6)
        # The day before launch continues the rebuilt line at the same scale.
        ratio = data.growth[k] / data.growth[k - 1]
        self.assertLess(abs(ratio - 1), 0.01)

    def test_buy_and_hold_adds_monthly_top_ups_on_first_trading_day(self):
        data = study.load_data()
        result = study.run(data, "1y", 1000, 100)
        spy = result["series"]["spy"]
        months = len({(d.year, d.month) for d in result["dates"]}) - 1
        self.assertAlmostEqual(spy["put_in"], 1000 + 100 * months)
        # SPY is flat, so value equals money put in.
        self.assertAlmostEqual(spy["final"], spy["put_in"], places=6)
        self.assertAlmostEqual(spy["gain"], 0, places=6)

    def test_short_timeframes_and_missing_history(self):
        data = study.load_data()
        self.assertIsNotNone(study.run(data, "1w", 1000, 0))
        self.assertIsNone(study.run(data, "5y", 1000, 0))
        week = study.run(data, "1w", 1000, 0)
        self.assertIsNone(week["series"]["nine_sig"]["annual"])

    def test_study_never_writes_real_records(self):
        counts = (MarketObservation.objects.count(), LedgerEntry.objects.count())
        with self.assertNumQueries(7):
            data = study.load_data()
        study.run(data, "1y", 1000, 50)
        self.assertEqual(counts, (MarketObservation.objects.count(), LedgerEntry.objects.count()))


class NineSigReplayTests(TestCase):
    def setUp(self):
        cache.clear()
        start = TODAY - timedelta(days=420)
        seed("SPY", start - timedelta(days=900), lambda d, i: 300.0)
        seed("GBPUSD=X", start - timedelta(days=900), lambda d, i: 1.0)
        # Growth rises 1% a day: well above the 9% quarterly line.
        seed("QQQ", start - timedelta(days=900), lambda d, i: 100.0 if d < start else 100 * (1.0033 ** (d - start).days))

    def test_quarterly_rule_sells_growth_into_safe_above_the_line(self):
        data = study.load_data()
        calls = []
        real = study.advice_for_holdings

        def spy_on(**kwargs):
            calls.append(kwargs)
            return real(**kwargs)

        with patch("tracker.study.advice_for_holdings", side_effect=spy_on):
            result = study.run(data, "1y", 1000, 0)
        nine = result["series"]["nine_sig"]
        self.assertGreaterEqual(len(calls), 3)
        self.assertTrue(all(c["as_of"].month in (3, 6, 9, 12) for c in calls))
        self.assertGreater(nine["trades"], 0)
        # Selling growth above the line leaves 9Sig behind 100% buy and hold.
        self.assertLess(nine["final"], result["series"]["hold"]["final"])
        # Rules see the same inputs as live reviews: pinned lines, pounds.
        self.assertEqual(calls[0]["equity_symbol"], "LQQ3.L")
        self.assertEqual(calls[0]["currency"], "GBP")


class DownloadTests(TestCase):
    def test_sync_stores_rows_and_skips_once_fresh(self):
        rows = [(TODAY - timedelta(days=1), D("10"), D("9.5")), (TODAY, D("11"), D("10.5"))]
        fetch = lambda symbol, since: rows
        results = sync_study_prices(fetch=fetch)
        self.assertEqual(results["SPY"], "2 days")
        self.assertEqual(StudyPrice.objects.filter(symbol="SPY").count(), 2)
        self.assertEqual(StudyPrice.objects.get(symbol="SPY", as_of=TODAY).adj_close, D("10.5"))
        self.assertEqual(sync_study_prices(fetch=fetch)["SPY"], "fresh")

    def test_failed_download_keeps_existing_rows(self):
        sync_study_prices(fetch=lambda symbol, since: [(TODAY, D("1"), D("1"))])

        def broken(symbol, since):
            raise OSError("offline")

        results = sync_study_prices(fetch=broken, force=True)
        self.assertTrue(results["QQQ"].startswith("failed"))
        self.assertEqual(StudyPrice.objects.filter(symbol="QQQ").count(), 1)


class StudyPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_user("study-owner", password="study-safe-password-1")
        self.other = get_user_model().objects.create_user("study-other", password="study-safe-password-2")
        preset = StrategyPreset.objects.get(slug="nasdaq-quarterly-target")
        self.mine = self.make(self.owner, preset, "My 9Sig")
        self.theirs = self.make(self.other, preset, "THEIR_PRIVATE_9SIG")
        self.client.force_login(self.owner)

    def make(self, owner, preset, name):
        account = Account.objects.create(owner=owner, name=name, kind="ISA", broker="TRADING212", country="GB")
        return StrategyInstance.objects.create(owner=owner, account=account, preset=preset, name=name,
                                               equity_symbol="LQQ3.L", safe_symbol="ERNS.L")

    def url(self, strategy, **params):
        base = reverse("v2_instance_page", args=[strategy.pk, "study"])
        return base + ("?" + "&".join("%s=%s" % kv for kv in params.items()) if params else "")

    def test_empty_state_before_download_and_owner_scope(self):
        page = self.client.get(self.url(self.mine))
        self.assertContains(page, "Hypothetical study, not your results")
        self.assertContains(page, "Study data not downloaded yet")
        self.assertEqual(self.client.get(self.url(self.theirs)).status_code, 404)

    def test_results_table_inputs_and_validation(self):
        start = TODAY - timedelta(days=400)
        seed("QQQ", start, lambda d, i: 100 * (1.0005 ** i))
        seed("SPY", start, lambda d, i: 200 + i * 0.1)
        seed("VTI", start, lambda d, i: 50 + i * 0.01)
        seed("GBPUSD=X", start, lambda d, i: 1.3)
        page = self.client.get(self.url(self.mine, amount="2000", monthly="50", range="1y"))
        self.assertContains(page, "9Sig value now")
        self.assertContains(page, "£2,000 + £50 a month")
        self.assertContains(page, "Every timeframe")
        self.assertContains(page, "Not enough history")  # 5, 10 and 20 years
        self.assertContains(page, "Includes simulated history")
        self.assertNotContains(page, "THEIR_PRIVATE_9SIG")
        bad = self.client.get(self.url(self.mine, amount="-5", range="nope"))
        self.assertContains(bad, "wasn't valid")
        self.assertEqual(bad.context["study_range"], "10y")
