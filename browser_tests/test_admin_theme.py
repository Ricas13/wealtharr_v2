"""The Django admin pages share the app's look: same dark background, a way back, nothing overflowing."""
import os
from pathlib import Path

from browser_tests.harness import BrowserHarness

APP_BACKGROUND = "rgb(13, 16, 20)"  # --w2-bg


class AdminThemeBrowserTests(BrowserHarness):
    def test_users_and_plans_pages_look_like_the_app_at_both_widths(self):
        self.owner.is_staff = self.owner.is_superuser = True
        self.owner.save()
        shots = os.environ.get("WEALTHARR_SCREENSHOT_DIR")
        for width in (360, 1280):
            for path in ("/django-admin/", "/django-admin/auth/user/", "/django-admin/tenancy/plan/",
                         "/django-admin/tenancy/plan/add/"):
                with self.subTest(width=width, path=path):
                    self.new_page(width)
                    self.sign_in()
                    self.page.goto(self.live_server_url + path, wait_until="networkidle")
                    body = self.page.evaluate("getComputedStyle(document.body).backgroundColor")
                    self.assertEqual(body, APP_BACKGROUND)
                    link = self.page.get_by_role("link", name="Back to the app")
                    self.assertTrue(link.is_visible())
                    dims = self.page.evaluate("({v: window.innerWidth, d: document.documentElement.scrollWidth})")
                    self.assertLessEqual(dims["d"], dims["v"] + 1, "horizontal overflow at %s on %d" % (path, width))
                    if shots:
                        name = path.strip("/").replace("/", "-") + "-%d.png" % width
                        self.page.screenshot(path=str(Path(shots) / name))
                    link.click()
                    self.page.wait_for_load_state("networkidle")
                    self.assertIn("/overview/", self.page.url)
        self.assertEqual(self.browser_errors, [])
