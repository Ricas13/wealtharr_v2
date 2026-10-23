"""Add strategy in a real browser: the past-performance comparison works above the setup form."""
import os
from datetime import timedelta
from pathlib import Path

from django.core.cache import cache

from browser_tests.harness import BrowserHarness
from tracker.test_study import TODAY, seed
from tracker.v2_home import OTHER_LINES


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
                self.assertEqual(rows.count(), 4 + len(OTHER_LINES))
                trend = page.locator("[data-rows] tr[data-key='nasdaq-trend-dca']")
                self.assertTrue(trend.is_visible())
                self.assertTrue(trend.locator("input[type='checkbox']").is_disabled())
                # Typed amounts: bigger than the old slider limits.
                page.locator("[data-amount]").fill("250000")
                with page.expect_response(lambda r: "amount=250000" in r.url):
                    page.locator("[data-amount]").fill("250000")
                    page.locator("[data-amount]").dispatch_event("input")
                page.wait_for_function("document.querySelector('[data-putin]').textContent.startsWith('251,')")
                # Sort by gain, best first, then flip.
                page.locator("[data-sort='gain_pct']").click()
                gains = page.locator("[data-compare] [data-rows] tr td:nth-child(4)").all_text_contents()
                values = [float(g.replace("+", "").replace("−", "-").rstrip("%"))
                          for g in gains if "—" not in g]
                self.assertEqual(values, sorted(values, reverse=True))
                self.assertEqual(page.locator("th:has([data-sort='gain_pct'])").get_attribute("aria-sort"), "descending")
                page.locator("[data-sort='gain_pct']").click()
                self.assertEqual(page.locator("th:has([data-sort='gain_pct'])").get_attribute("aria-sort"), "ascending")
                page.locator("[data-sort='label']").click()
                names = page.locator("[data-compare] [data-rows] th").all_text_contents()
                self.assertEqual(names, sorted(names, key=str.lower))
                # The same timeline now recomputes table gains, returns and
                # cash-flow-neutral drawdown for precisely the selected dates.
                nav = page.locator("[data-zoom-track]")
                start_handle = page.locator('[data-zoom-handle="start"]')
                full_period = page.locator("[data-zoom-dates]").inner_text()
                initial_value = page.locator("[data-rows] tr[data-key='nine_sig']").locator("td").nth(1).inner_text()
                initial_gain = page.locator("[data-rows] tr[data-key='nine_sig']").locator("td").nth(2).inner_text()
                self.assertEqual(start_handle.get_attribute("aria-valuenow"), "0")
                nav.scroll_into_view_if_needed()
                track_box = nav.bounding_box()
                handle_box = start_handle.bounding_box()
                start_x = handle_box["x"] + handle_box["width"] / 2
                y = track_box["y"] + track_box["height"] / 2
                page.mouse.move(start_x, y)
                page.mouse.down()
                page.mouse.move(track_box["x"] + track_box["width"] * .4, y, steps=8)
                page.mouse.up()
                self.assertGreater(int(start_handle.get_attribute("aria-valuenow")), 0)
                self.assertNotEqual(page.locator("[data-zoom-dates]").inner_text(), full_period)
                page.wait_for_function("document.querySelector('[data-table-status]').textContent.includes('Showing results for the selected dates')")
                # Zoomed simulations are indexed back to the chosen start
                # amount, rather than claiming the older £100k balance was
                # the investment made on the zoom's first day.
                self.assertIn("Rebased start + selected top-ups",
                              page.locator("[data-putin-title]").inner_text())
                self.assertIn("not a fresh strategy backtest",
                              page.locator("[data-rebased-note]").inner_text())
                self.assertNotEqual(
                    page.locator("[data-rows] tr[data-key='nine_sig']").locator("td").nth(1).inner_text(),
                    initial_value)
                self.assertNotEqual(page.locator("[data-rows] tr[data-key='nine_sig']").locator("td").nth(2).inner_text(),
                                    initial_gain)
                # Main chart tooltip at its selected left boundary must begin
                # at the entered 250,000 pounds for every available strategy.
                start_chart = page.locator("[data-chart]").bounding_box()
                page.mouse.move(start_chart["x"] + 1, start_chart["y"] + start_chart["height"] / 2)
                page.wait_for_timeout(80)
                first_tooltip = page.locator("[data-tip]")
                self.assertTrue(first_tooltip.is_visible())
                self.assertIn("£250,000", first_tooltip.inner_text())
                self.assertIn("Money put in by then: £250,000", first_tooltip.inner_text())
                # Shorter than 360 days: no misleading annualised return.
                self.assertIn("—", page.locator("[data-rows] tr[data-key='nine_sig']").locator("td").nth(3).inner_text())
                # Move the selected time window with its keyboard slider.
                window = page.locator("[data-zoom-window]")
                before = int(window.get_attribute("aria-valuenow"))
                window.focus()
                page.keyboard.press("ArrowRight")
                self.assertGreaterEqual(int(window.get_attribute("aria-valuenow")), before)
                # Reset restores the complete range and all graph dates.
                page.locator("[data-zoom-reset]").click()
                self.assertEqual(start_handle.get_attribute("aria-valuenow"), "0")
                self.assertEqual(page.locator("[data-zoom-dates]").inner_text(), full_period)
                self.assertTrue(page.locator("[data-zoom-reset]").is_disabled())
                self.assertEqual(page.locator("[data-rows] tr[data-key='nine_sig']").locator("td").nth(1).inner_text(),
                                 initial_value)
                self.assertEqual(page.locator("[data-rows] tr[data-key='nine_sig']").locator("td").nth(2).inner_text(),
                                 initial_gain)
                # Hover readout.
                svg = page.locator("[data-compare] [data-chart]")
                svg.scroll_into_view_if_needed()
                box = svg.bounding_box()
                page.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + box["height"] / 2)
                tip = page.locator("[data-tip]")
                self.assertTrue(tip.is_visible())
                self.assertEqual(tip.locator(".tip-row").count(), 5)
                self.assertIn("Money put in by then", tip.text_content())
                self.assertTrue(tip.locator(".tip-date").text_content())
                # Log explains itself.
                page.locator("[data-scale='log']").click()
                self.assertIn("equal % growth", page.locator("[data-scale-hint]").text_content())
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
