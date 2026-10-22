"""Real-browser Study zoom for both 9Sig and a fixed-weight strategy."""
from datetime import timedelta

from django.core.cache import cache
from django.urls import reverse

from browser_tests.harness import BrowserHarness
from tracker.models import Account, StrategyInstance, StrategyPreset
from tracker.test_study import TODAY, seed
from tracker.test_study_fixed import seed_all_weather


class StudyZoomBrowserTests(BrowserHarness):
    def setUp(self):
        super().setUp()
        cache.clear()
        start = TODAY - timedelta(days=800)
        seed("QQQ", start, lambda d, i: 100 * (1.0006 ** i))
        seed_all_weather(start, start)
        account = Account.objects.create(owner=self.owner, name="ISA", kind="ISA",
                                         broker="TRADING212", country="GB")
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=account, preset=self.preset,
            name="Study 9Sig", equity_symbol="LQQ3.L", safe_symbol="ERNS.L")

    def test_study_zooms_cards_and_table_at_desktop_and_mobile(self):
        for width in (1440, 360):
            with self.subTest(width=width):
                page = self.new_page(width)
                self.sign_in()
                url = self.live_server_url + reverse(
                    "v2_instance_page", args=[self.strategy.pk, "study"]) + "?range=1y&monthly=100"
                page.goto(url, wait_until="networkidle")
                self.assertTrue(page.locator("[data-study-track]").is_visible())
                self.assertTrue(page.locator("[data-study-chart]").is_visible())
                self.assertEqual(page.locator("[data-study-zoom]").count(), 1)
                self.assertEqual(page.locator('[name="range"][value="max"]').count(), 1)
                self.assertEqual(page.locator('[name="range"][value="20y"]').count(), 0)
                original_gain = page.locator('[data-study-primary-metric="gain"]').inner_text()
                original_value = page.locator('[data-study-primary-metric="final"]').inner_text()
                original_all_frames = page.get_by_text("Every timeframe · value now").inner_text()
                track = page.locator("[data-study-track]")
                handle = page.locator('[data-study-handle="start"]')
                track.scroll_into_view_if_needed()
                bounds = track.bounding_box()
                h = handle.bounding_box()
                x, y = h["x"] + h["width"] / 2, bounds["y"] + bounds["height"] / 2
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(bounds["x"] + bounds["width"] * .4, y, steps=8)
                page.mouse.up()
                self.assertGreater(int(handle.get_attribute("aria-valuenow")), 0)
                page.wait_for_function(
                    "document.querySelector('[data-study-status]').textContent.includes('Selected-period figures use')")
                self.assertNotEqual(page.locator('[data-study-primary-metric="gain"]').inner_text(), original_gain)
                # Adjusting just the start does not change the end-date portfolio value.
                self.assertEqual(page.locator('[data-study-primary-metric="final"]').inner_text(), original_value)
                self.assertEqual(page.get_by_text("Every timeframe · value now").inner_text(), original_all_frames)
                self.assertEqual(page.locator('[data-study-primary-metric="annual"]').inner_text(), "—")
                # Moving end changes the ending value.
                end = page.locator('[data-study-handle="end"]')
                end.focus()
                page.keyboard.press("Shift+ArrowLeft")
                page.wait_for_function(
                    "document.querySelector('[data-study-status]').textContent.includes('Selected-period figures use')")
                self.assertNotEqual(page.locator('[data-study-primary-metric="final"]').inner_text(), original_value)
                svg = page.locator("[data-study-chart]")
                svg.scroll_into_view_if_needed()
                chart = svg.bounding_box()
                page.mouse.move(chart["x"] + chart["width"] * .5, chart["y"] + chart["height"] * .5)
                self.assertTrue(page.locator("[data-study-tip]").is_visible())
                # Switching log scale does not reset the selection.
                selected = handle.get_attribute("aria-valuenow")
                page.get_by_role("link", name="Log", exact=True).click()
                self.assertEqual(handle.get_attribute("aria-valuenow"), selected)
                page.locator("[data-study-reset]").click()
                self.assertEqual(handle.get_attribute("aria-valuenow"), "0")
                self.assertTrue(page.locator("[data-study-reset]").is_disabled())
                self.assertEqual(page.locator('[data-study-primary-metric="gain"]').inner_text(), original_gain)
                self.assertEqual(page.locator('[data-study-primary-metric="final"]').inner_text(), original_value)
                self.assert_reachable_mobile()

    def test_all_weather_study_uses_same_navigator(self):
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="all-weather", defaults={"name": "All Weather", "active": True})
        account = Account.objects.create(owner=self.owner, name="AW ISA", kind="ISA",
                                         broker="TRADING212", country="GB")
        strategy = StrategyInstance.objects.create(
            owner=self.owner, account=account, preset=preset, name="All Weather",
            strategy_key="all-weather")
        page = self.new_page(390)
        self.sign_in()
        url = self.live_server_url + reverse(
            "v2_instance_page", args=[strategy.pk, "study"]) + "?range=1y"
        page.goto(url, wait_until="networkidle")
        self.assertTrue(page.locator("[data-study-track]").is_visible())
        page.locator('[data-study-handle="end"]').focus()
        page.keyboard.press("Shift+ArrowLeft")
        page.wait_for_function(
            "document.querySelector('[data-study-status]').textContent.includes('Selected-period figures use')")
        self.assertTrue(page.locator('[data-study-row="strategy"]').is_visible())
        self.assert_reachable_mobile()
