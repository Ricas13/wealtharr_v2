"""Phase 7 real browser smoke: scoped Strategy and Settings at 1440px/390px."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.test import override_settings

from browser_tests.test_owner_journeys import BrowserHarness
from tracker.models import Account, StrategyInstance, StrategyPreset


@override_settings(TURNSTILE_REQUIRED=False)
class Phase7BrowserTests(BrowserHarness):
    def setUp(self):
        super().setUp()
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "9Sig", "risk": "HIGH",
                      "summary": "Fixed rules", "active": True},
        )
        a = Account.objects.create(
            owner=self.owner, name="Phase 7 ISA", kind="ISA",
            country="GB", broker="TRADING212",
        )
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=a, preset=preset,
            name="My Phase7 9Sig", strategy_key="nasdaq-quarterly-target",
            platform_code="TRADING212", account_type_code="ISA",
            tracking_only=True, equity_symbol="LQQ3.L", safe_symbol="ERNS.L",
            alert_routes={
                "quarterly": {"email": True, "discord": False},
                "unacted": {"email": False, "discord": False},
                "urgent": {"email": True, "discord": False},
                "contribution": {"email": False, "discord": False},
                "data": {"email": False, "discord": True},
            },
        )
        other = Account.objects.create(
            owner=self.other, name="SECRET_OTHER_PROFILE", kind="GIA",
            country="GB", broker="TRADING212",
        )
        StrategyInstance.objects.create(
            owner=self.other, account=other, preset=preset,
            name="SECRET_OTHER_9SIG",
        )

    def test_real_strategy_and_settings_mobile_desktop(self):
        keep = os.getenv("WEALTHARR_CAPTURE_V2_BASELINES") == "1"
        screenshot_dir = Path(settings.BASE_DIR) / "docs/design/screenshots"
        with TemporaryDirectory() as tmp:
            for width in (1440, 390):
                with self.subTest(width=width):
                    self.new_page(width)
                    self.sign_in()

                    def snapshot(name):
                        self.assertLessEqual(
                            self.page.evaluate(
                                "document.documentElement.scrollWidth"
                            ), width + 1,
                        )
                        self.assertNotIn("SECRET_OTHER_9SIG", self.page.content())
                        self.assertNotIn("SECRET_OTHER_PROFILE", self.page.content())
                        dest = (screenshot_dir if keep else Path(tmp)) / (
                            f"phase7-{name}-{width}.png"
                        )
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        self.page.screenshot(path=str(dest), full_page=True)
                        self.assertTrue(dest.exists())

                    self.visit("v2_instance_page", self.strategy.pk, "strategy")
                    self.page.get_by_role(
                        "heading", name="How this strategy works"
                    ).wait_for()
                    self.page.get_by_text("Account check pending").first.wait_for()
                    snapshot("strategy")
                    self.page.locator('input[name="unacted_email"]').check()
                    self.page.get_by_role(
                        "button", name="Save strategy alerts"
                    ).click()
                    self.page.locator('input[name="unacted_email"]').wait_for()
                    self.assertTrue(
                        self.page.locator('input[name="unacted_email"]').is_checked()
                    )
                    self.visit("v2_settings")
                    self.page.get_by_role(
                        "heading", name="Notification destinations"
                    ).wait_for()
                    snapshot("settings")
                    self.page.locator('input[name="unacted_reminder_days"]').fill("8")
                    self.page.get_by_role(
                        "button", name="Save destinations"
                    ).click()
                    self.page.locator('input[name="unacted_reminder_days"]').wait_for()
                    self.assertEqual(
                        self.page.locator(
                            'input[name="unacted_reminder_days"]'
                        ).input_value(), "8",
                    )
                    self.assertEqual(self.browser_errors, [])
