"""Owner admin links: at the bottom of the normal sidebar for staff only, at phone and desktop widths."""
import os
from pathlib import Path

from browser_tests.harness import BrowserHarness


class AdminSidebarBrowserTests(BrowserHarness):
    def test_staff_see_admin_links_below_their_normal_menu(self):
        self.owner.is_staff = self.owner.is_superuser = True
        self.owner.save()
        shots = os.environ.get("WEALTHARR_SCREENSHOT_DIR")
        for width in (1280, 360):
            with self.subTest(width=width):
                self.new_page(width)
                self.sign_in()
                self.visit("control")
                self.assertTrue(self.page.get_by_role("heading", name="Admin").is_visible())
                admin = self.page.locator("nav[aria-label='Admin']")
                if width <= 900:
                    self.page.locator("#v2-mobile-nav").click()
                self.assertTrue(admin.get_by_role("link", name="Users").is_visible())
                self.assertTrue(self.page.get_by_role("link", name="Alerts & settings").is_visible())
                personal = self.page.get_by_role("link", name="Alerts & settings").bounding_box()
                self.assertGreater(admin.bounding_box()["y"], personal["y"])
                if shots:
                    self.page.screenshot(path=str(Path(shots) / ("admin-%d.png" % width)))
                self.assert_reachable_mobile()

    def test_regular_users_have_no_admin_links(self):
        self.new_page(1280)
        self.sign_in()
        self.visit("v2_overview")
        self.assertEqual(self.page.locator("nav[aria-label='Admin']").count(), 0)
