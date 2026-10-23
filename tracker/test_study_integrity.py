"""Study data integrity: unit conversion, bad rows, stale prices and fair comparisons."""
import io
import json
from datetime import datetime, time, timedelta, timezone as utc_timezone
from decimal import Decimal as D
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from tracker.study_data import fetch_study_chart
from tracker.test_study import TODAY, seed, weekdays


def _payload(currency, closes, adjusted, days=None):
    days = days or [TODAY - timedelta(days=len(closes) - i) for i in range(len(closes))]
    stamps = [int(datetime.combine(d, time(16), utc_timezone.utc).timestamp()) for d in days]
    return {"chart": {"result": [{
        "meta": {"currency": currency}, "timestamp": stamps,
        "indicators": {"quote": [{"close": closes}], "adjclose": [{"adjclose": adjusted}]},
    }]}}


def _fetch(symbol, payload):
    body = io.BytesIO(json.dumps(payload).encode())
    with patch("tracker.study_data.urlopen", return_value=body):
        return fetch_study_chart(symbol, TODAY - timedelta(days=30))


class DownloadUnitTests(SimpleTestCase):
    def test_pence_line_with_a_missing_adjusted_close_is_converted_once(self):
        rows = _fetch("LQQ3.L", _payload("GBp", [5000, 5100, 5200], [5000, None, 5200]))
        self.assertEqual([r[2] for r in rows], [D("50"), D("51"), D("52")])
        self.assertEqual([r[1] for r in rows], [D("50"), D("51"), D("52")])

    def test_mismatched_lengths_are_refused(self):
        payload = _payload("USD", [10, 11, 12], [10, 11, 12])
        payload["chart"]["result"][0]["indicators"]["adjclose"][0]["adjclose"] = [10, 11]
        with self.assertRaises(ValueError):
            _fetch("SPY", payload)

    def test_an_isolated_bad_row_is_left_out(self):
        rows = _fetch("SPY", _payload("USD", [100, 101, 1.02, 103, 104], [100, 101, 1.02, 103, 104]))
        self.assertEqual([r[1] for r in rows], [D("100"), D("101"), D("103"), D("104")])
        # A bad first or last row is also left out.
        rows = _fetch("SPY", _payload("USD", [1, 100, 101, 102], [1, 100, 101, 102]))
        self.assertEqual(rows[0][1], D("100"))
        rows = _fetch("SPY", _payload("USD", [100, 101, 102, 1], [100, 101, 102, 1]))
        self.assertEqual(rows[-1][1], D("102"))

    def test_a_lasting_hundredfold_jump_is_refused(self):
        with self.assertRaises(ValueError):
            _fetch("SGLN.L", _payload("GBp", [3000, 3010, 30.2, 30.3, 30.4], [3000, 3010, 30.2, 30.3, 30.4]))

    def test_large_real_moves_are_kept(self):
        rows = _fetch("TQQQ", _payload("USD", [100, 65, 90, 60], [100, 65, 90, 60]))
        self.assertEqual(len(rows), 4)


class NineSigFreshnessTests(TestCase):
    def setUp(self):
        cache.clear()
        start = TODAY - timedelta(days=500)
        seed("QQQ", start, lambda d, i: 100 * (1.001 ** i))
        seed("SPY", start, lambda d, i: 200.0)
        seed("VTI", start, lambda d, i: 50.0)
        seed("GBPUSD=X", start, lambda d, i: 1.25)

    def test_stale_growth_prices_end_the_study_instead_of_carrying_forward(self):
        from tracker import study
        stop = TODAY - timedelta(days=60)
        seed("LQQ3.L", TODAY - timedelta(days=300), lambda d, i: 30 + i * 0.01, currency="GBP", end=stop)
        data = study.load_data()
        self.assertLessEqual(data.days[-1], stop + timedelta(days=7))
        self.assertTrue(all(v is not None for v in data.growth))
        result = study.run(data, "1y", 1000, 0)
        self.assertEqual(result["end"], data.days[-1])

    def test_a_stale_benchmark_is_left_out_not_flattened(self):
        from tracker import study
        StudyPrice = __import__("tracker.models", fromlist=["StudyPrice"]).StudyPrice
        StudyPrice.objects.filter(symbol="VTI", as_of__gt=TODAY - timedelta(days=40)).delete()
        data = study.load_data()
        result = study.run(data, "1y", 1000, 0)
        self.assertIsNone(result["series"]["vti"])
        self.assertIsNotNone(result["series"]["spy"])

    def test_the_100_up_check_reads_real_tqqq_closes_once_they_exist(self):
        from tracker import study
        launch = TODAY - timedelta(days=200)
        seed("TQQQ", launch, lambda d, i: 40 + i * 0.05)
        data = study.load_data()
        self.assertGreaterEqual(data.reference_real_from, launch)
        refs = dict(data.reference)
        k = data.days.index(data.reference_real_from)
        self.assertEqual(refs[data.days[k]], D("40.000000"))
        self.assertEqual(refs[data.days[-1]], D(str(round(40 + (len(list(weekdays(launch, TODAY))) - 1) * 0.05, 6))))
        # Before launch, the rebuilt line is scaled to meet TQQQ without a jump.
        self.assertLess(abs(refs[data.days[k - 1]] / refs[data.days[k]] - 1), D("0.01"))
        result = study.run(data, "1y", 1000, 0)
        self.assertTrue(any("TQQQ" in note for note in result["simulated_notes"]))


class NineSigResetDisclosureTests(TestCase):
    def setUp(self):
        cache.clear()
        start = TODAY - timedelta(days=1400)
        seed("SPY", start, lambda d, i: 300.0)
        seed("VTI", start, lambda d, i: 50.0)
        seed("GBPUSD=X", start, lambda d, i: 1.0)
        seed("QQQ", start, lambda d, i: 100.0)
        # TQQQ more than doubles within one quarter about a year ago.
        jump = TODAY - timedelta(days=330)
        seed("TQQQ", start, lambda d, i: 50.0 if d < jump else 120.0)

    def test_resets_are_counted_and_shown_as_an_assumption(self):
        from tracker import study
        data = study.load_data()
        result = study.run(data, "1y", 1000, 0)
        self.assertGreaterEqual(result["assumed_resets"], 1)
        self.assertTrue(any("treated as confirmed" in line for line in study.how_it_works(data)))


class HomepageSamePeriodTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_a_strategy_whose_prices_stop_early_is_shown_without_fake_returns(self):
        from tracker.models import StudyPrice
        from tracker.test_study_fixed import seed_all_weather
        start = TODAY - timedelta(days=800)
        seed("QQQ", start, lambda d, i: 100 * (1.001 ** i))
        seed_all_weather(start, start)
        StudyPrice.objects.filter(symbol="VUAG.L", as_of__gt=TODAY - timedelta(days=60)).delete()
        data = self.client.get("/compare.json?amount=1000&monthly=0&range=1y").json()
        unavailable = next(r for r in data["rows"] if r["key"] == "all-weather")
        self.assertFalse(unavailable["available"])
        self.assertIsNone(unavailable["final"])
        self.assertEqual(unavailable["points"], [])
        self.assertIn("All-season mix", data["left_out"])
        page = self.client.get("/?range=1y")
        self.assertContains(page, "Historical figures unavailable")
        self.assertContains(page, "money-weighted (XIRR)")

    def test_compared_rows_share_the_same_dates(self):
        from tracker.test_study_fixed import seed_all_weather
        start = TODAY - timedelta(days=800)
        seed("QQQ", start, lambda d, i: 100 * (1.001 ** i))
        seed_all_weather(start, start)
        data = self.client.get("/compare.json?amount=1000&monthly=0&range=1y").json()
        self.assertIn("all-weather", [r["key"] for r in data["rows"]])
        self.assertNotIn("All-season mix", data["left_out"])
        self.assertTrue(data["period"])
        row = next(r for r in data["rows"] if r["key"] == "all-weather")
        self.assertEqual(len(row["points"]), len(data["put_in_points"]))


class FixedStudyFreshnessTests(TestCase):
    def test_a_fund_that_stops_ends_the_study_on_its_last_fresh_day(self):
        from tracker.models import StudyPrice
        from tracker.strategies.registry import module_for_key
        from tracker.test_study_fixed import seed_all_weather
        cache.clear()
        start = TODAY - timedelta(days=800)
        seed_all_weather(start, start)
        stop = TODAY - timedelta(days=60)
        StudyPrice.objects.filter(symbol="GLTL.L", as_of__gt=stop).delete()
        engine = module_for_key("all-weather").study["GBP"]
        data = engine.load_data()
        self.assertLessEqual(data.days[-1], stop + timedelta(days=7))
        result = engine.run(data, "1y", 1000, 0)
        self.assertEqual(result["end"], data.days[-1])
        values = result["series"]["strategy"]["values"]
        self.assertEqual(len(values), len(result["dates"]))
