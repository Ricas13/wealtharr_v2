"""Add strategy in a real browser: the past-performance comparison works above the setup form."""
import os
from datetime import timedelta
from pathlib import Path

from django.core.cache import cache

from browser_tests.harness import BrowserHarness
from tracker.test_study import TODAY, seed


class AddStrategyCompareBrowserTests(BrowserHarness):
    def setUp(self):
        super().setUp()
        cache.clear()
        from tracker.test_study_fixed import seed_all_weather
        start = TODAY - timedelta(days=800)
        seed("QQQ", start, lambda d, i: 100 * (1.001 ** i))
        seed_all_weather(start, start)

    def test_comparison_is_interactive_at_360_and_1280(self):
        shots = os.environ.get("WEALTHARR_SCREENSHOT_DIR")
        for width in (1440, 360):
            with self.subTest(width=width):
                page = self.new_page(width)
                self.sign_in()
                self.visit("v2_add_strategy")
                # The seeded history is about two years: 10Y (the default) has too little.
                self.assertTrue(page.locator("[data-compare] [data-empty]").is_visible())
                with page.expect_response(lambda r: "add-strategy/compare.json" in r.url and "range=1y" in r.url):
                    page.locator("[data-range='1y']").click()
                page.wait_for_function("document.querySelector('[data-range-words]').textContent === '1 year'")
                rows = page.locator("[data-compare] [data-rows] tr")
                self.assertEqual(rows.count(), 5)  # 9Sig, buy & hold, SPY, VTI, All Seasons
                page.get_by_label("Show All Seasons").click()
                self.assertEqual(page.locator("[data-compare] tr.off").count(), 1)
                self.assertEqual(page.locator("[data-compare] .chart polyline").count(), 5)
                # The setup choices sit above it: side by side on desktop, stacked on phones.
                self.assertTrue(page.locator("select[name='strategy']").is_visible())
                cards = [page.locator(".w2-wizard-card").nth(i).bounding_box() for i in range(3)]
                chart = page.locator("#past-performance").bounding_box()
                self.assertLess(max(c["y"] for c in cards), chart["y"])
                if width > 1200:
                    self.assertAlmostEqual(cards[0]["y"], cards[2]["y"], delta=2)
                else:
                    self.assertLess(cards[0]["y"], cards[2]["y"])
                if shots:
                    page.screenshot(path=str(Path(shots) / ("add-layout-%d.png" % width)), full_page=True)
                self.assert_reachable_mobile()
