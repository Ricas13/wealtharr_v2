"""Signed-out homepage in a real browser: example review, comparison and invite."""
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache

from browser_tests.harness import BrowserHarness
from tracker.models import InviteRequest
from tracker.test_study import TODAY, seed


class HomepageBrowserTests(BrowserHarness):
    def setUp(self):
        super().setUp()
        cache.clear()
        start = TODAY - timedelta(days=800)
        seed("QQQ", start, lambda d, i: 100 * (1.001 ** i))
        seed("SPY", start, lambda d, i: 200.0)
        seed("VTI", start, lambda d, i: 50.0)
        seed("GBPUSD=X", start, lambda d, i: 1.25)

    def overflow(self):
        return self.page.evaluate("document.documentElement.scrollWidth - window.innerWidth")

    @patch("tracker.v2_home.dispatch")
    def test_homepage_is_interactive_at_390_and_1440(self, dispatch):
        for width in (390, 1440):
            with self.subTest(width=width):
                page = self.new_page(width)
                page.goto(self.live_server_url + "/?range=1y", wait_until="networkidle")
                self.assertLessEqual(self.overflow(), 1)
                # Example review: nudge -> trade -> done -> replay.
                page.locator(".notif").click()
                self.assertTrue(page.get_by_text("Growth ran ahead").is_visible())
                page.get_by_role("button", name="I did this").click()
                self.assertTrue(page.get_by_text("Back on target").is_visible())
                self.assertEqual(page.locator("[data-mix-growth]").text_content(), "60")
                page.get_by_role("button", name="↺ Replay").click()
                self.assertTrue(page.locator(".notif").is_visible())
                # Comparison: real study rows, untick one, change timeframe and top-up.
                self.assertEqual(page.locator("[data-rows] tr").count(), 4)
                self.assertEqual(page.locator(".chart polyline").count(), 5)
                self.assertTrue(page.locator("[data-zoom-track]").is_visible())
                page.locator('[data-zoom-handle="end"]').focus()
                page.keyboard.press("Shift+ArrowLeft")
                self.assertFalse(page.locator("[data-zoom-reset]").is_disabled())
                # The table now shows results for the selected dates, then
                # restores full-period figures on reset.
                page.wait_for_function("document.querySelector('[data-table-status]').textContent.includes('Showing results for the selected dates')")
                selected_rows = page.locator("[data-rows]").inner_text()
                page.locator("[data-zoom-reset]").click()
                self.assertNotEqual(page.locator("[data-rows]").inner_text(), selected_rows)
                self.assertIn("full selected timeframe", page.locator("[data-table-status]").inner_text())
                page.get_by_label("Show S&P 500 (SPY)").click()
                self.assertEqual(page.locator("tr.off").count(), 1)
                self.assertEqual(page.locator(".chart polyline").count(), 4)
                with page.expect_response(lambda r: "compare.json" in r.url and "range=ytd" in r.url):
                    page.locator("[data-range='ytd']").click()
                page.locator("[data-monthly]").fill("0")
                with page.expect_response(lambda r: "monthly=0" in r.url):
                    page.locator("[data-monthly]").dispatch_event("input")
                page.wait_for_function("document.querySelector('[data-putin]').textContent === '1,000' && document.querySelector('[data-range-words]').textContent === 'this year'")
                self.assertEqual(page.locator("tr.off").count(), 1, "Unticked rows stay unticked")
                # How it works.
                page.get_by_role("button", name="3 Follow the nudges").click()
                self.assertTrue(page.get_by_text("Prices are checked hourly").is_visible())
                self.assertEqual(self.browser_errors, [])
        # Invite request.
        self.page.locator("#invite-email").fill("friend@example.com")
        self.page.locator(".invite").get_by_role("button", name="Request an invite").click()
        self.page.wait_for_load_state("networkidle")
        self.assertTrue(self.page.get_by_text("Thanks, you're on the list.").is_visible())
        self.assertTrue(InviteRequest.objects.filter(email="friend@example.com").exists())

    def test_guides_render_without_overflow_or_errors(self):
        from tracker.guides import GUIDES
        for width in (390, 1440):
            page = self.new_page(width)
            page.goto(self.live_server_url + "/guides/", wait_until="networkidle")
            self.assertLessEqual(self.overflow(), 1)
            page.get_by_role("link", name=GUIDES[1]["h1"]).first.click()
            page.wait_for_load_state("networkidle")
            self.assertTrue(page.get_by_role("heading", level=1, name=GUIDES[1]["h1"]).is_visible())
            self.assertLessEqual(self.overflow(), 1)
            page.get_by_text("Does volatility decay mean leveraged ETFs always lose?").click()
            self.assertTrue(page.get_by_text("In steady trends").is_visible())
        self.assertEqual(self.browser_errors, [])
