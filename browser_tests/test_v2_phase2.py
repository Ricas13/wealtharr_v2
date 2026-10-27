"""Phase 2 actual Chromium journey for code-defined enrolment.

Run in the existing browser CI job. Capture source-backed layout screenshots at
1440px and 390px. Pixel equivalence to the prototype's fictional financial
data is deliberately NOT asserted; compare shared shell and wizard intent.
Set WEALTHARR_CAPTURE_V2_BASELINES=1 to write reviewed captures.
"""
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.test import override_settings

from browser_tests.harness import BrowserHarness


@override_settings(TURNSTILE_REQUIRED=False)
class V2OnboardingBrowserTests(BrowserHarness):
    def test_one_screen_setup_at_desktop_and_mobile(self):
        output = Path(settings.BASE_DIR) / "docs/design/screenshots"
        keep = os.environ.get("WEALTHARR_CAPTURE_V2_BASELINES") == "1"
        with TemporaryDirectory() as tmp:
            for width in (1440, 390):
                with self.subTest(width=width):
                    self.new_page(width)
                    self.sign_in()

                    def screenshot(name):
                        self.assertLessEqual(
                            self.page.evaluate("document.documentElement.scrollWidth"),
                            width + 1,
                        )
                        self.assert_compact_chrome(width)
                        target = (output if keep else Path(tmp)) / (
                            f"phase2-{name}-{width}.png"
                        )
                        target.parent.mkdir(parents=True, exist_ok=True)
                        self.page.screenshot(path=str(target), full_page=True)
                        self.assertTrue(target.is_file())


                    self.visit("v2_add_strategy")
                    self.page.get_by_role("heading", name="Add a strategy").wait_for()
                    screenshot("setup")
                    # Everything is on one page: strategy, account, funds, start, go live.
                    # Both released strategies are offered; 9Sig is preselected.
                    self.assertFalse(self.page.locator(
                        'select[name="strategy"] option[value="all-weather"]').evaluate("(el) => el.disabled"))
                    self.assertEqual(self.page.locator('select[name="strategy"]').input_value(),
                                     "nasdaq-quarterly-target")
                    # Only the chosen broker's account types are offered (Trading 212 has no SIPP).
                    self.assertEqual(self.page.locator('input[name="account_type"][value="SIPP"]').count(), 0)
                    self.assertTrue(self.page.locator(
                        'input[name="account_type"][value="ISA"]').is_checked())
                    self.assertTrue(self.page.get_by_text("IE00BLRPRL42").first.is_visible())
                    # Picking All Weather shows its five funds instead.
                    with self.page.expect_navigation():
                        self.page.locator('select[name="strategy"]').select_option("all-weather")
                    self.assertTrue(self.page.get_by_text("IE00BFMXXD54").first.is_visible())
                    self.assertEqual(self.page.locator('select[name="strategy"]').input_value(), "all-weather")
                    with self.page.expect_navigation():
                        self.page.locator('select[name="strategy"]').select_option("nasdaq-quarterly-target")
                    self.page.locator('input[name="ack"]').check()
                    self.page.locator('input[name="mode"][value="hold"]').check()
                    self.assertFalse(
                        self.page.locator("#w2-hold-fields").evaluate("(el) => el.hidden"))
                    self.page.locator('input[name="mode"][value="fresh"]').check()
                    self.assertTrue(
                        self.page.locator("#w2-hold-fields").evaluate("(el) => el.hidden"))
                    self.page.locator('input[name="name"]').fill("Browser 9Sig " + str(width))
                    self.page.locator('input[name="amount"]').fill("1000.00")
                    self.page.locator('input[name="deposited"]').check()
                    self.page.get_by_role("button", name="Create strategy").click()
                    self.page.get_by_role("link", name="Dashboard").wait_for()
                    # "Go live now" is ticked by default, so a cash start asks for the opening buys.
                    self.page.get_by_text("Opening position from the rules", exact=False).first.wait_for()
                    self.assertNotIn("OTHER_PERSON_PRIVATE_STRATEGY", self.page.content())
                    self.assertEqual(self.browser_errors, [])
