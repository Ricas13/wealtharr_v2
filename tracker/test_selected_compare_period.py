"""Cash-flow-aware historical comparisons are independent of chart zooming."""
from datetime import date, timedelta

from django.test import SimpleTestCase

from tracker.study import _xirr, start_index, StudyData
from tracker.v2_home import FRAMES, selected_period_metrics


def comparison():
    start = date(2024, 1, 2)
    days = [(start + timedelta(days=i)).isoformat() for i in range(401)]
    points = [100.0] * 200 + [150.0] * 200 + [140.0]
    invested = [100.0] * 200 + [150.0] * 201
    return {
        "ready": True, "rows": [
            {"key": "strategy", "points": points, "annual": 2.0, "final": 140.0},
            {"key": "flat", "points": [100.0] * 200 + [150.0] * 201, "annual": 0.0,
             "final": 150.0},
        ],
        "point_dates_iso": days, "put_in_points": invested,
    }


class SelectedPeriodTests(SimpleTestCase):
    def test_compare_presets_keep_ytd_and_max_but_remove_twenty_year_button(self):
        self.assertEqual([key for key, _ in FRAMES], ["ytd", "1y", "5y", "10y", "max"])

    def test_max_starts_at_first_available_day(self):
        days = [date(2023, 4, 3), date(2023, 4, 4), date(2024, 4, 4)]
        data = StudyData(days=days)
        self.assertEqual(start_index(data, "max"), 0)
        self.assertIsNone(start_index(StudyData(), "max"))

    def test_full_period_reconciles_to_inception_value_and_xirr(self):
        data = comparison()
        out = selected_period_metrics(data, 0, 400)
        strategy = out["rows"]["strategy"]
        self.assertEqual(out["put_in"], 150.0)
        self.assertEqual(strategy["final"], 140.0)
        self.assertEqual(strategy["gain_pct"], -6.67)
        self.assertEqual(strategy["drawdown"], -6.67)
        start = date(2024, 1, 2)
        flows = [(start, 100.0), (start + timedelta(days=200), 50.0)]
        self.assertEqual(strategy["annual"], round(_xirr(flows, start + timedelta(days=400), 140), 2))
        self.assertEqual(out["rows"]["flat"]["drawdown"], 0.0)

    def test_opening_value_replaces_earlier_deposits(self):
        data = comparison()
        out = selected_period_metrics(data, 250, 400)
        # The first 250 days of deposits are in the £150 opening value.
        self.assertEqual(out["put_in"], 150.0)
        self.assertEqual(out["rows"]["strategy"]["gain_pct"], -6.67)
        self.assertIsNone(out["rows"]["strategy"]["annual"])
        self.assertEqual(out["rows"]["flat"]["gain_pct"], 0.0)
        self.assertEqual(out["rows"]["flat"]["drawdown"], 0.0)
        self.assertEqual(data["rows"][0]["final"], 140.0)  # Original payload unchanged.
        self.assertEqual(data["rows"][0]["annual"], 2.0)

    def test_contribution_on_opening_day_is_not_counted_twice(self):
        data = comparison()
        out = selected_period_metrics(data, 200, 400)
        self.assertEqual(out["put_in"], 150.0)
        self.assertEqual(out["rows"]["strategy"]["gain_pct"], -6.67)

    def test_rejects_invalid_indices_or_misaligned_series(self):
        data = comparison()
        for a, b in ((-1, 30), (40, 41), (1, 450), (301, 300)):
            with self.subTest(start=a, end=b), self.assertRaises(ValueError):
                selected_period_metrics(data, a, b)
        data["rows"][0]["points"] = [100.0]
        with self.assertRaises(ValueError):
            selected_period_metrics(data, 1, 400)
