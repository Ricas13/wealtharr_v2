"""Phase 7 real Chromium 390/1440 Settings and strategy-routing acceptance."""
from pathlib import Path
from tempfile import TemporaryDirectory
import os

from django.conf import settings
from django.test import override_settings

from browser_tests.test_owner_journeys import BrowserHarness
from tracker.models import Account, StrategyInstance, StrategyPreset


@override_settings(TURNSTILE_REQUIRED=False)
class Phase7SettingsBrowserTests(BrowserHarness):
    def setUp(self):
        super().setUp()
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "9Sig", "risk": "HIGH",
                      "summary": "Owner strategy", "active": True},
        )
        account = Account.objects.create(
            owner=self.owner, name="Settings test ISA",
            country="GB", kind="ISA", broker="TRADING212",
        )
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=account, preset=preset,
            name="Owner alert test", equity_symbol="LQQ3.L",
            safe_symbol="ERNS.L", strategy_key="nasdaq-quarterly-target",
            tracking_only=True, alert_routes={
                kind: {"email": True, "discord": False}
                for kind in ("quarterly", "urgent", "data",
                             "unacted", "contribution")
            },
        )
        other_account = Account.objects.create(
            owner=self.other, name="Other hidden ISA",
            country="GB", kind="ISA", broker="TRADING212",
        )
        StrategyInstance.objects.create(
            owner=self.other, account=other_account, preset=preset,
            name="CROSS_OWNER_PRIVATE_SECRET",
            strategy_key="nasdaq-quarterly-target", tracking_only=True,
        )

    def test_settings_are_real_and_editable_at_mobile_and_desktop(self):
        screenshots = Path(settings.BASE_DIR) / "docs/design/screenshots"
        save_baselines = os.getenv("WEALTHARR_CAPTURE_V2_BASELINES") == "1"
        with TemporaryDirectory() as tmp:
            for width in (390, 1440):
                with self.subTest(width=width):
                    self.new_page(width)
                    self.sign_in()
                    self.visit("v2_settings")
                    self.page.get_by_role(
                        "heading", name="Notification destinations"
                    ).wait_for()
                    self.assertNotIn(
                        "CROSS_OWNER_PRIVATE_SECRET", self.page.content()
                    )
                    self.page.locator(
                        'input[name="destination_email"]'
                    ).fill("receipt@example.test")
                    self.page.locator('input[name="email_enabled"]').check()
                    self.page.get_by_role(
                        "button", name="Save notification preferences"
                    ).click()
                    self.assertIn("/settings/", self.page.url)
                    self.page.get_by_text(
                        "Notification destinations", exact=True
                    ).wait_for()
                    strategy_form = self.page.locator(
                        'form:has(input[name="strategy_id"][value="%s"])'
                        % self.strategy.pk
                    )
                    strategy_form.locator(
                        'input[name="route_urgent_email"]'
                    ).uncheck()
                    strategy_form.get_by_role(
                        "button", name="Save Owner alert test alerts"
                    ).click()
                    self.assertIn("/settings/", self.page.url)
                    self.assertFalse(self.page.locator(
                        'form:has(input[name="strategy_id"][value="%s"]) '
                        'input[name="route_urgent_email"]' % self.strategy.pk
                    ).is_checked())
                    self.assertLessEqual(
                        self.page.evaluate(
                            "document.documentElement.scrollWidth"
                        ), width + 1,
                    )
                    output = (screenshots if save_baselines else Path(tmp))
                    output.mkdir(parents=True, exist_ok=True)
                    img = output / ("phase7-settings-%s.png" % width)
                    self.page.screenshot(path=str(img), full_page=True)
                    self.assertTrue(img.is_file())
                    self.assertEqual(self.browser_errors, [])
