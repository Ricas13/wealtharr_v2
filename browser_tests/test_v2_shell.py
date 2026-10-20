"""Browser check of the compact v2 chrome on every owner route.

Run with the existing Chromium browser job. Screenshots are captured at both
requested viewports for every v2 route. Set WEALTHARR_CAPTURE_V2_BASELINES=1
when intentionally writing reviewed PNGs to docs/design/screenshots/.

"""
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.test import override_settings

from browser_tests.harness import BrowserHarness
from tracker.models import Account, StrategyInstance, StrategyPreset


@override_settings(TURNSTILE_REQUIRED=False)
class V2ShellBrowserTests(BrowserHarness):
    def setUp(self):
        super().setUp()
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "9Sig", "risk": "HIGH", "summary": "Fixed rules", "active": True},
        )
        account = Account.objects.create(
            owner=self.owner, name="Owner test ISA",
            kind="ISA", broker="TRADING212", country="GB",
        )
        self.instance = StrategyInstance.objects.create(
            owner=self.owner, account=account, preset=preset, name="Real owner 9Sig",
        )
        other_account = Account.objects.create(
            owner=self.other, name="Other ISA", kind="ISA",
            broker="TRADING212", country="GB",
        )
        StrategyInstance.objects.create(
            owner=self.other, account=other_account, preset=preset,
            name="OTHER_PERSON_PRIVATE_STRATEGY",
        )

    def test_all_phase_one_pages_two_viewports_and_prototype_chrome(self):
        routes = [
            ("overview", "v2_overview", None),
            ("today", "v2_instance_page", [self.instance.pk, "today"]),
            ("history", "v2_instance_page", [self.instance.pk, "history"]),
            ("ledger", "v2_instance_page", [self.instance.pk, "ledger"]),
            ("strategy", "v2_instance_page", [self.instance.pk, "strategy"]),
            ("breakdown", "v2_instance_page", [self.instance.pk, "breakdown"]),
            ("catalogue", "v2_catalogue", None),
            ("settings", "v2_settings", None),
            ("add-strategy", "v2_add_strategy", None),
        ]
        output_dir = Path(settings.BASE_DIR) / "docs/design/screenshots"
        save_baselines = os.environ.get("WEALTHARR_CAPTURE_V2_BASELINES") == "1"
        with TemporaryDirectory() as temporary:
            for width in (1440, 390):
                self.new_page(width)
                self.sign_in()
                for label, name, args in routes:
                    with self.subTest(width=width, page=label):
                        self.visit(name, *(args or []))
                        self.page.locator("#v2-main h1").first.wait_for()
                        self.assertNotIn(
                            "OTHER_PERSON_PRIVATE_STRATEGY", self.page.content()
                        )
                        self.assertLessEqual(
                            self.page.evaluate("document.documentElement.scrollWidth"),
                            width + 1,
                            "Horizontal page overflow: %s at %s" % (label, width),
                        )
                        # Compact chrome: a 196px rail on desktop; a 44px top
                        # bar with the menu button only on phones.
                        if width > 900:
                            self.assertFalse(self.page.locator(".w2-topbar").is_visible())
                            rail = self.page.locator(".w2-sidebar").bounding_box()
                            self.assertAlmostEqual(rail["width"], 196, delta=2)
                        else:
                            bar = self.page.locator(".w2-topbar").bounding_box()
                            self.assertAlmostEqual(bar["height"], 44, delta=2)
                        filename = "phase1-%s-%s.png" % (label, width)
                        target = (
                            output_dir if save_baselines else Path(temporary)
                        ) / filename
                        target.parent.mkdir(parents=True, exist_ok=True)
                        self.page.screenshot(path=str(target), full_page=True)
                        self.assertTrue(target.is_file())

                if width == 390:
                    self.visit("v2_overview")
                    menu = self.page.locator("#v2-mobile-nav")
                    menu.click()
                    self.assertEqual(menu.get_attribute("aria-expanded"), "true")
                    self.page.get_by_role("navigation", name="Primary navigation").get_by_role(
                        "link", name="Instrument catalogue"
                    ).click()
                    self.page.get_by_role(
                        "heading", name="Instrument catalogue", level=1
                    ).wait_for()
                    self.assertEqual(menu.get_attribute("aria-expanded"), "false")
                    menu.click()
                    self.page.keyboard.press("Escape")
                    self.assertEqual(menu.get_attribute("aria-expanded"), "false")
                else:
                    self.visit("v2_overview")
                    collapse = self.page.locator("#v2-collapse")
                    collapse.click()
                    self.assertEqual(collapse.get_attribute("aria-pressed"), "true")
                    self.page.reload()
                    expand = self.page.locator("#v2-collapse")
                    self.assertEqual(expand.get_attribute("aria-pressed"), "true")
                    expand.click()
                    self.assertEqual(expand.get_attribute("aria-pressed"), "false")
                self.assertEqual(self.browser_errors, [])
