"""Users and Plans in the app shell, phone and desktop: browse, filter, open, edit, create."""
import os
from pathlib import Path

from browser_tests.harness import BrowserHarness
from tenancy.models import Plan, UserAccess


class ConsoleBrowserTests(BrowserHarness):
    def setUp(self):
        super().setUp()
        self.owner.is_staff = self.owner.is_superuser = True
        self.owner.save()
        self.pro = Plan.objects.create(slug="pro", name="Pro", is_public=True, price_label="£5 / month")
        UserAccess.objects.create(user=self.other, plan=self.pro)
        self.shots = os.environ.get("WEALTHARR_SCREENSHOT_DIR")

    def shot(self, name, width):
        if self.shots:
            self.page.screenshot(path=str(Path(self.shots) / ("console-%s-%d.png" % (name, width))), full_page=True)

    def test_browse_filter_and_edit_a_user(self):
        for width in (360, 1280):
            with self.subTest(width=width):
                self.new_page(width)
                self.sign_in()
                self.page.goto(self.live_server_url + "/console/users/", wait_until="networkidle")
                self.assertTrue(self.page.get_by_role("heading", name="Users").is_visible())
                self.assertTrue(self.page.get_by_role("link", name="other-browser").is_visible())
                self.shot("users", width)
                self.page.get_by_label("Plan", exact=True).select_option(str(self.pro.pk))
                self.page.get_by_role("button", name="Filter").click()
                self.page.wait_for_load_state("networkidle")
                self.assertTrue(self.page.get_by_role("link", name="other-browser").is_visible())
                self.assertEqual(self.page.get_by_role("link", name="owner-browser").count(), 0)
                self.assert_reachable_mobile()
                self.page.get_by_role("link", name="other-browser").click()
                self.page.wait_for_load_state("networkidle")
                self.shot("user", width)
                self.page.locator("#id_access-note").fill("browser note")
                self.page.get_by_role("button", name="Save plan and features").click()
                self.page.wait_for_load_state("networkidle")
                self.assertTrue(self.page.get_by_text("Plan and features saved.").is_visible())
                self.assertEqual(UserAccess.objects.get(user=self.other).note, "browser note")
                self.assertTrue(self.page.get_by_role("link", name="Advanced (Django admin)").is_visible())
                self.assert_reachable_mobile()

    def test_plans_list_and_create(self):
        for width in (360, 1280):
            with self.subTest(width=width):
                Plan.objects.filter(slug="starter").delete()
                self.new_page(width)
                self.sign_in()
                self.page.goto(self.live_server_url + "/console/plans/", wait_until="networkidle")
                self.assertTrue(self.page.get_by_role("link", name="Pro").is_visible())
                self.shot("plans", width)
                self.page.get_by_role("link", name="New plan").click()
                self.page.wait_for_load_state("networkidle")
                self.page.locator("#id_name").fill("Starter")
                self.page.locator("#id_slug").fill("starter")
                self.page.locator("#id_price_label").fill("£3 / month")
                self.page.get_by_label("Number of strategies").fill("2")
                self.page.get_by_label("Email alerts (alerts.email)").check()
                self.shot("plan", width)
                self.page.get_by_role("button", name="Save plan").click()
                self.page.wait_for_load_state("networkidle")
                self.assertTrue(self.page.get_by_text("Plan saved.").is_visible())
                plan = Plan.objects.get(slug="starter")
                self.assertEqual((plan.limits, plan.features), ({"strategies.max": 2}, ["alerts.email"]))
                self.assert_reachable_mobile()
