"""Phase 2 actual Chromium journey for code-defined catalogue and enrollment.

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

from browser_tests.test_owner_journeys import BrowserHarness


@override_settings(TURNSTILE_REQUIRED=False)
class V2OnboardingBrowserTests(BrowserHarness):
    def test_catalogue_and_all_five_onboarding_steps_at_desktop_and_mobile(self):
        prototype = (Path(settings.BASE_DIR) / "docs/design/app_v2.html").read_text(
            encoding="utf-8"
        )
        output = Path(settings.BASE_DIR) / "docs/design/screenshots"
        keep = os.environ.get("WEALTHARR_CAPTURE_V2_BASELINES") == "1"
        with TemporaryDirectory() as tmp:
            for width in (1440, 390):
                with self.subTest(width=width):
                    self.new_page(width)
                    self.sign_in()
                    ref = self.ctx.new_page()
                    ref.route("https://fonts.googleapis.com/**", lambda route: route.abort())
                    ref.route("https://fonts.gstatic.com/**", lambda route: route.abort())
                    ref.set_content(prototype, wait_until="domcontentloaded")
                    expected_bar = ref.locator(".topbar").bounding_box()
                    ref.close()

                    def screenshot(name):
                        self.assertLessEqual(
                            self.page.evaluate("document.documentElement.scrollWidth"),
                            width + 1,
                        )
                        bar = self.page.locator(".w2-topbar").bounding_box()
                        self.assertAlmostEqual(bar["height"], expected_bar["height"], delta=2)
                        target = (output if keep else Path(tmp)) / (
                            f"phase2-{name}-{width}.png"
                        )
                        target.parent.mkdir(parents=True, exist_ok=True)
                        self.page.screenshot(path=str(target), full_page=True)
                        self.assertTrue(target.is_file())

                    self.visit("v2_catalogue")
                    self.page.get_by_role("heading", name="Instrument catalogue").wait_for()
                    self.page.get_by_text("Account check pending").first.wait_for()
                    self.assertTrue(
                        self.page.get_by_text("IE00BLRPRL42").count() >= 1
                    )
                    screenshot("catalogue")

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
                    self.page.get_by_role("heading", name="Today").wait_for()
                    self.page.get_by_text("Tracking only", exact=False).first.wait_for()
                    self.assertNotIn("OTHER_PERSON_PRIVATE_STRATEGY", self.page.content())
                    self.assertEqual(self.browser_errors, [])
