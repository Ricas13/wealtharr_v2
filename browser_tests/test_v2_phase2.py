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
from django.utils import timezone

from browser_tests.harness import BrowserHarness


@override_settings(TURNSTILE_REQUIRED=False)
class V2OnboardingBrowserTests(BrowserHarness):
    def test_catalogue_and_all_five_onboarding_steps_at_desktop_and_mobile(self):
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
                    self.page.get_by_text("step 1 of 5").wait_for()
                    screenshot("strategy")
                    self.assertTrue(
                        self.page.locator(
                            'input[name="strategy"][value="all-weather"]'
                        ).is_disabled()
                    )
                    self.page.locator(
                        'input[name="strategy"][value="nasdaq-quarterly-target"]'
                    ).check()
                    self.page.get_by_role("button", name="Continue").click()
                    self.page.get_by_text("step 2 of 5").wait_for()
                    self.assertTrue(
                        self.page.locator('input[name="account_type"][value="SIPP"]')
                        .is_disabled()
                    )
                    self.page.locator(
                        'input[name="account_type"][value="ISA"]'
                    ).check()
                    self.page.get_by_role("button", name="Continue").click()
                    self.page.get_by_text("step 3 of 5").wait_for()
                    screenshot("instruments")
                    self.page.locator('input[name="ack"]').check()
                    self.page.get_by_role("button", name="Continue").click()
                    self.page.get_by_text("step 4 of 5").wait_for()
                    self.page.locator('input[name="mode"][value="hold"]').check()
                    self.assertFalse(
                        self.page.locator("#w2-hold-fields").evaluate(
                            "(el) => el.hidden"
                        )
                    )
                    self.page.locator('input[name="mode"][value="fresh"]').check()
                    self.assertTrue(
                        self.page.locator("#w2-hold-fields").evaluate(
                            "(el) => el.hidden"
                        )
                    )
                    self.page.locator('input[name="name"]').fill(
                        "Browser 9Sig " + str(width)
                    )
                    self.page.locator('input[name="start"]').fill(
                        timezone.localdate().isoformat()
                    )
                    self.page.locator('input[name="amount"]').fill("1000.00")
                    self.page.locator('input[name="deposited"]').check()
                    screenshot("starting-point")
                    self.page.get_by_role("button", name="Continue").click()
                    self.page.get_by_text("step 5 of 5").wait_for()
                    screenshot("alerts")
                    self.page.get_by_role("button", name="Create strategy").click()
                    self.page.get_by_role("link", name="Dashboard").wait_for()
                    # "Go live now" is ticked by default, so a cash start asks for the opening buys.
                    self.page.get_by_text("Buy your opening position", exact=False).first.wait_for()
                    self.assertNotIn("OTHER_PERSON_PRIVATE_STRATEGY", self.page.content())
                    self.assertEqual(self.browser_errors, [])
