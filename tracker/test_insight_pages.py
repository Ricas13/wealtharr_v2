"""Chart readout, rule what-if, review explanation and price evidence."""
import json
from datetime import date, timedelta
from decimal import Decimal as D

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from tracker import v2_dashboard as dash
from tracker.models import MarketObservation, StrategySignal, StudyPrice
from tracker.strategies.nasdaq_trend_dca import plugin as nasdaq, v1
from tracker.test_review_fixes import make_instance


def weekdays(count, first=date(2020, 1, 1)):
    out, d = [], first
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


class ChartReadoutTests(SimpleTestCase):
    def points(self):
        today = timezone.localdate()
        return [(today - timedelta(days=10 - i), D(100 + i * 5), D(100 + (i // 4) * 30)) for i in range(11)]

    def test_value_mode_has_a_reading_per_day_and_marks_when_under_the_money_put_in(self):
        pts = self.points()
        pts[3] = (pts[3][0], D("90"), D("130"))
        pts[4] = (pts[4][0], D("92"), D("130"))
        result = dash.chart(pts, span="all", mode="gbp")
        readout = json.loads(result["readout_json"])
        self.assertEqual(len(readout), len(pts))
        self.assertEqual(readout[0]["d"], pts[0][0].isoformat())
        self.assertTrue(any(t.startswith("Value ") for t in readout[0]["t"]))
        self.assertTrue(any(t.startswith("Net deposits ") for t in readout[0]["t"]))
        self.assertTrue(result["underwater"])

    def test_a_deposit_that_pushes_money_in_above_the_value_does_not_invert_the_shading(self):
        today = timezone.localdate()
        # Day 0 is below its money in (95 < 100). On day 1 a 30 deposit lifts money in to 130
        # while the value (105) is still above the earlier 100: that stretch must not be shaded.
        pts = [(today - timedelta(days=3), D("95"), D("100")), (today - timedelta(days=2), D("105"), D("130")),
               (today - timedelta(days=1), D("110"), D("130")), (today, D("112"), D("130"))]
        result = dash.chart(pts, span="all", mode="gbp")
        self.assertEqual(len(result["underwater"]), 2)  # not 3: the first stretch is skipped

    def test_no_shading_when_value_is_above_money_in(self):
        pts = [(d, v + 50, n) for d, v, n in self.points()]
        self.assertEqual(dash.chart(pts, span="all", mode="gbp")["underwater"], [])


class WhatIfTests(TestCase):
    def seed(self, spy_rule):
        days = weekdays(520)
        StudyPrice.objects.bulk_create([
            StudyPrice(symbol=s, as_of=d, close=D(str(p)), adj_close=D(str(p)), currency="USD")
            for s in ("SPY", "QQQ") for d, p in ((d, spy_rule(i)) for i, d in enumerate(days))])

    def test_defaults_reproduce_the_released_rules_and_wider_bands_switch_less(self):
        self.seed(lambda i: 100 + (i % 60) * (1 if i % 120 < 60 else -1) * 0.8 + i * 0.05)
        base = nasdaq.simulate({})
        self.assertEqual(base["changes"], nasdaq.simulate({"enter": "1.04", "exit": "0.97"})["changes"])
        wide = nasdaq.simulate({"enter": "1.12", "exit": "0.90"})
        self.assertLessEqual(wide["changes"], base["changes"])
        self.assertIn("Hypothetical", base["note"])

    def test_values_outside_the_allowed_ranges_are_refused(self):
        self.seed(lambda i: 100 + i * 0.1)
        for params in ({"enter": "0.5"}, {"exit": "1.2"}, {"enter": "abc"}, {"reduce": "1.5", "cash_guard": "1.3"}):
            with self.assertRaises(ValueError):
                nasdaq.simulate(params)

    def test_no_history_is_reported_not_invented(self):
        with self.assertRaises(ValueError):
            nasdaq.simulate({})

    def test_endpoint_is_owner_scoped_and_only_for_strategies_with_a_what_if(self):
        self.seed(lambda i: 100 + i * 0.1)
        mine = make_instance("sim-owner", "nasdaq-trend-dca", "Mine")
        self.client.force_login(mine.owner)
        ok = self.client.get(reverse("v2_study_simulate_json", args=[mine.pk]), {"enter": "1.05"})
        self.assertEqual(ok.status_code, 200)
        self.assertIn("changes", ok.json())
        bad = self.client.get(reverse("v2_study_simulate_json", args=[mine.pk]), {"enter": "9"})
        self.assertEqual(bad.status_code, 400)
        other = make_instance("sim-other", "nasdaq-trend-dca", "Theirs")
        self.assertEqual(self.client.get(reverse("v2_study_simulate_json", args=[other.pk])).status_code, 404)
        plain = make_instance("sim-plain", "all-weather", "Plain")
        self.client.force_login(plain.owner)
        self.assertEqual(self.client.get(reverse("v2_study_simulate_json", args=[plain.pk])).status_code, 400)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("v2_study_simulate_json", args=[mine.pk])).status_code, 302)


class ReviewExplanationTests(TestCase):
    def test_evidence_lists_each_close_and_flags_unverified_ones(self):
        from tracker.v2_today import price_evidence
        strategy = make_instance("evidence-owner", "all-weather")
        day = timezone.localdate() - timedelta(days=2)
        MarketObservation.objects.create(symbol="VUAG.L", as_of=day, price=D("80"), currency="GBP",
                                         source="yahoo", raw_unit_verified=True, raw_quote_unit="GBP",
                                         raw_close=D("80"))
        MarketObservation.objects.create(symbol="IGLT.L", as_of=day, price=D("12"), currency="GBP")
        signal = StrategySignal.objects.create(
            strategy=strategy, signal_key="ev:1", kind="QUARTERLY", as_of=day, title="t", message="m",
            actions=[
                {"side": "BUY", "symbol": "VUAG.L", "quantity": "1", "estimated_gbp": "80",
                 "price_gbp": "80", "price_date": day.isoformat()},
                {"side": "BUY", "symbol": "IGLT.L", "quantity": "1", "estimated_gbp": "12",
                 "price_gbp": "12", "price_date": day.isoformat()},
                {"side": "BUY", "symbol": "GLTL.L", "quantity": "1", "estimated_gbp": "5"}])
        rows = price_evidence(signal)
        self.assertEqual([r["verified"] for r in rows], [True, False, False])
        self.assertEqual([r["found"] for r in rows], [True, True, False])

    def test_a_stored_close_that_differs_from_the_price_used_is_not_verified(self):
        from tracker.v2_today import price_evidence
        strategy = make_instance("evidence-diff", "all-weather")
        day = timezone.localdate() - timedelta(days=2)
        MarketObservation.objects.create(symbol="VUAG.L", as_of=day, price=D("80"), currency="GBP",
                                         source="yahoo", raw_unit_verified=True, raw_quote_unit="GBP",
                                         raw_close=D("80"))
        signal = StrategySignal.objects.create(
            strategy=strategy, signal_key="ev:2", kind="QUARTERLY", as_of=day, title="t", message="m",
            actions=[{"side": "BUY", "symbol": "VUAG.L", "quantity": "1", "estimated_gbp": "90",
                      "price_gbp": "90", "price_date": day.isoformat()}])
        row = price_evidence(signal)[0]
        self.assertIs(row["matches"], False)
        self.assertFalse(row["verified"])

    def test_an_observation_in_another_currency_is_not_evidence(self):
        from tracker.v2_today import price_evidence
        strategy = make_instance("evidence-cur", "all-weather")
        day = timezone.localdate() - timedelta(days=2)
        MarketObservation.objects.create(symbol="VUAG.L", as_of=day, price=D("80"), currency="USD",
                                         source="yahoo", raw_unit_verified=True, raw_quote_unit="USD",
                                         raw_close=D("80"))
        signal = StrategySignal.objects.create(
            strategy=strategy, signal_key="ev:3", kind="QUARTERLY", as_of=day, title="t", message="m",
            actions=[{"side": "BUY", "symbol": "VUAG.L", "quantity": "1", "price_gbp": "80",
                      "price_date": day.isoformat()}])
        self.assertFalse(price_evidence(signal)[0]["found"])

    def test_without_a_price_in_the_action_the_stored_close_is_shown_and_labelled(self):
        from tracker.v2_today import price_evidence
        strategy = make_instance("evidence-stored", "all-weather")
        day = timezone.localdate() - timedelta(days=2)
        MarketObservation.objects.create(symbol="VUAG.L", as_of=day, price=D("80"), currency="GBP",
                                         source="yahoo", raw_unit_verified=True, raw_quote_unit="GBP",
                                         raw_close=D("80"))
        signal = StrategySignal.objects.create(
            strategy=strategy, signal_key="ev:4", kind="QUARTERLY", as_of=day, title="t", message="m",
            actions=[{"side": "BUY", "symbol": "VUAG.L", "quantity": "1", "price_date": day.isoformat()}])
        row = price_evidence(signal)[0]
        self.assertEqual((row["price"], row["price_is_stored"], row["verified"]), (D("80"), True, True))

    def test_nasdaq_review_explains_itself_from_the_same_closes(self):
        strategy = make_instance("why-owner", "nasdaq-trend-dca", "Why")
        days = weekdays(260)
        rows = []
        for i, d in enumerate(days):
            for symbol, value in (("SPY", 100 if i < 220 else 120), ("QQQ", 100)):
                rows.append(MarketObservation(
                    symbol=symbol, as_of=d, price=D(value), currency="USD", source="yahoo",
                    raw_unit_verified=True, raw_quote_unit="USD", raw_close=D(value)))
        MarketObservation.objects.bulk_create(rows)
        signal = StrategySignal.objects.create(
            strategy=strategy, signal_key="why:1", kind="QUARTERLY", as_of=days[-1], title="t", message="m",
            actions=[])
        detail = nasdaq.review_detail(strategy, signal)
        flat = " ".join(str(v) for sec in detail["sections"] for _, v in sec["rows"])
        self.assertIn("3x Nasdaq", flat)
        self.assertIn("of its 200-day average", flat)
        self.assertIn("104.0% / 97.0%", flat)
        self.assertEqual(detail["sections"][0]["title"], "Why this allocation")
        # A date the closes don't cover can't be explained: say so, don't guess.
        gone = nasdaq.review_detail(strategy, StrategySignal(as_of=days[-1] + timedelta(days=30)))
        self.assertEqual(gone["sections"], [])
        self.assertTrue(gone["trace_error"])


class StudyPageWhatIfTests(TestCase):
    def test_the_study_page_offers_the_what_if_only_where_a_plugin_has_one(self):
        from tracker.test_study import seed
        today = timezone.localdate()
        start = today - timedelta(days=900)
        for symbol, currency in (("SPY", "USD"), ("QQQ", "USD"), ("TQQQ", "USD"), ("GBPUSD=X", "USD"),
                                 ("LQQ3.L", "GBP"), ("EQQQ.L", "GBP")):
            seed(symbol, start, lambda d, i: 100 + i * 0.05 + (i % 40) * 0.3, currency=currency)
        mine = make_instance("sim-page", "nasdaq-trend-dca", "Sim page")
        self.client.force_login(mine.owner)
        page = self.client.get(reverse("v2_instance_page", args=[mine.pk, "study"]))
        self.assertContains(page, "What if the rule edges were different?")
        self.assertContains(page, "Hypothetical.")
        self.assertContains(page, 'name="enter"')
        plain = make_instance("sim-page-2", "all-weather", "No what-if")
        self.client.force_login(plain.owner)
        self.assertNotContains(self.client.get(reverse("v2_instance_page", args=[plain.pk, "study"])),
                               "What if the rule edges")
