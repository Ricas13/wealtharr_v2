"""Add strategy shows the hypothetical comparison, with the strategies' own names, before setup."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from tracker.test_study import TODAY, seed


class AddStrategyCompareTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user("owner", password="a-long-test-password-1")
        self.client.force_login(self.user)

    def seed_history(self):
        from tracker.test_study_fixed import seed_all_weather
        start = TODAY - timedelta(days=800)
        seed("QQQ", start, lambda d, i: 100 * (1.001 ** i))
        seed_all_weather(start, start)

    def test_add_page_shows_the_comparison_with_real_names(self):
        self.seed_history()
        page = self.client.get(reverse("v2_add_strategy"))
        self.assertContains(page, "How each strategy did in the past")
        self.assertContains(page, 'data-compare-url="%s"' % reverse("v2_compare_json"))
        self.assertContains(page, "js/compare.js")
        self.assertContains(page, "css/compare.css")
        self.assertContains(page, "9Sig")
        self.assertContains(page, "All Seasons")
        self.assertContains(page, "3x Nasdaq Trend + DCA")
        # The default 10-year table has no rows with only ~2 years seeded.
        # The named 1-year API supplies the same table once comparable prices exist.
        named = self.client.get(
            reverse("v2_compare_json") + "?amount=1000&monthly=0&range=1y"
        ).json()
        self.assertIn("nasdaq-trend-dca", [r["key"] for r in named["rows"]])
        self.assertContains(page, "Hypothetical, not anyone")
        # The three setup choices come first, side by side; the comparison sits below them.
        body = page.content.decode()
        self.assertEqual(body.count("w2-panel w2-wizard-card\""), 3)
        self.assertLess(body.index('name="strategy"'), body.index("How each strategy did in the past"))
        self.assertLess(body.index("Create strategy"), body.index("How each strategy did in the past"))

    def test_named_endpoint_needs_sign_in_and_uses_real_names(self):
        self.seed_history()
        data = self.client.get(reverse("v2_compare_json") + "?amount=1000&monthly=0&range=1y").json()
        labels = [r["label"] for r in data["rows"]]
        self.assertIn("9Sig", labels)
        self.assertIn("All Seasons", labels)
        self.assertIn("3x Nasdaq Trend + DCA", labels)
        self.assertNotIn("All-season mix", labels)
        nasdaq = next(r for r in data["rows"] if r["key"] == "nasdaq-trend-dca")
        self.assertIsNone(nasdaq["final"])
        self.assertEqual(nasdaq["points"], [])
        self.client.logout()
        self.assertEqual(self.client.get(reverse("v2_compare_json")).status_code, 302)

    def test_public_homepage_keeps_generic_names(self):
        self.seed_history()
        self.client.logout()
        data = self.client.get("/compare.json?amount=1000&monthly=0&range=1y").json()
        labels = [r["label"] for r in data["rows"]]
        self.assertIn("Quarterly signal", labels)
        self.assertIn("All-season mix", labels)
        self.assertIn("Daily Nasdaq trend + DCA", labels)
        page = self.client.get("/?range=1y")
        self.assertNotContains(page, "9Sig")
        self.assertContains(page, "js/compare.js")

    def test_selected_window_api_requires_login_and_has_same_labels(self):
        self.seed_history()
        path = reverse("v2_compare_json") + "?amount=1000&monthly=0&range=1y"
        result = self.client.get(path).json()
        self.assertTrue(result["ready"])
        selected = self.client.get(path + "&window_start=5&window_end=125")
        self.assertEqual(selected.status_code, 200)
        values = selected.json()
        self.assertEqual((values["start_index"], values["end_index"]), (5, 125))
        self.assertIn("nine_sig", values["rows"])
        self.assertEqual(set(values["rows"]["nine_sig"]), {"final", "gain_pct", "annual", "drawdown"})
        self.assertIsNone(values["rows"]["nine_sig"]["annual"])  # Short selection
        self.assertEqual(self.client.get(path + "&window_start=-1&window_end=5").status_code, 400)
        self.assertEqual(self.client.get(path + "&window_start=5&window_end=999999").status_code, 400)
        self.client.logout()
        self.assertEqual(self.client.get(path + "&window_start=5&window_end=125").status_code, 302)

    def test_zoom_rebases_to_entered_start_not_accumulated_portfolio(self):
        from datetime import date, timedelta
        from tracker.v2_home import selected_period_metrics

        days = [(date(2026, 1, 1) + timedelta(days=i)).isoformat()
                for i in range(4)]
        comparison = {
            "ready": True, "amount": 1000,
            "point_dates_iso": days,
            "put_in_points": [1000, 1000, 1100, 1100],
            "rows": [
                {"key": "nine_sig", "label": "9Sig", "points": [1000, 1100, 1200, 1350]},
                {"key": "spy", "label": "SPY", "points": [1000, 1000, 1100, 1000]},
                {"key": "missing", "label": "Missing", "points": []},
            ],
        }
        metrics = selected_period_metrics(comparison, 1, 3)
        self.assertEqual(metrics["put_in"], 1100)
        self.assertEqual(metrics["reference_label"], "Selected start amount")
        # Jan 2: rebased £1,000. Jan 3: portfolio earns no return,
        # receives £100. Jan 4: 12.5% on £1,100 = £1,237.50.
        self.assertEqual(metrics["rows"]["nine_sig"]["final"], 1237.50)
        self.assertEqual(metrics["rows"]["nine_sig"]["gain_pct"], 12.50)
        self.assertEqual(metrics["rows"]["nine_sig"]["drawdown"], 0)
        self.assertEqual(metrics["rows"]["nine_sig"]["annual"], None)
        self.assertEqual(metrics["rows"]["spy"]["final"], 1000)
        self.assertLess(metrics["rows"]["spy"]["drawdown"], 0)
        self.assertNotIn("missing", metrics["rows"])
        # No monthly top-up is double counted just because the zoom moves.

    def test_zoom_api_uses_entered_amount_instead_of_older_holdings(self):
        self.seed_history()
        path = reverse("v2_compare_json") + "?amount=2500&monthly=100&range=1y"
        comparison = self.client.get(path).json()
        self.assertTrue(comparison["ready"])
        indices = (60, 125)
        selected = self.client.get(
            path + "&window_start=%d&window_end=%d" % indices
        ).json()
        invested = comparison["put_in_points"]
        expected = 2500 + invested[indices[1]] - invested[indices[0]]
        self.assertAlmostEqual(selected["put_in"], expected, places=2)
        self.assertNotEqual(
            selected["put_in"], comparison["rows"][0]["points"][indices[0]] +
            invested[indices[1]] - invested[indices[0]]
        )

    def test_other_pages_do_not_load_the_comparison(self):
        page = self.client.get(reverse("v2_overview"))
        self.assertNotContains(page, "js/compare.js")
