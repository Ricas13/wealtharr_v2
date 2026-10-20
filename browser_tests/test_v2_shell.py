"""Phase 1 browser reference: actual Django chrome vs approved prototype chrome.

Run with the existing Chromium browser job. Screenshots are captured at both
requested viewports for every v2 route. Set WEALTHARR_CAPTURE_V2_BASELINES=1
when intentionally writing reviewed PNGs to docs/design/screenshots/.

The prototype contains synthetic data, so visual comparison is intentionally
limited to its shared shell dimensions; dashboard content pixel equality would
be both misleading and incorrect at this phase.
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
        prototype = (Path(settings.BASE_DIR) / "docs/design/app_v2.html").read_text(
            encoding="utf-8"
        )
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
                # The reference is HTML with inlined CSS/JS. Block font requests
                # so the comparison remains offline and deterministic in CI.
                reference = self.ctx.new_page()
                reference.route("https://fonts.googleapis.com/**", lambda route: route.abort())
                reference.route("https://fonts.gstatic.com/**", lambda route: route.abort())
                reference.set_content(prototype, wait_until="domcontentloaded")
                reference.evaluate("window.location.hash = 'overview'")
                ref_bar = reference.locator(".topbar").bounding_box()
                ref_rail = reference.locator(".rail").bounding_box()
                self.assertIsNotNone(ref_bar)
                self.assertIsNotNone(ref_rail)

                for label, name, args in routes:
                    with self.subTest(width=width, page=label):
                        self.visit(name, *(args or []))
                        self.page.locator(".w2-page-heading h1").wait_for()
                        self.assertNotIn(
                            "OTHER_PERSON_PRIVATE_STRATEGY", self.page.content()
                        )
                        self.assertLessEqual(
                            self.page.evaluate("document.documentElement.scrollWidth"),
                            width + 1,
                            "Horizontal page overflow: %s at %s" % (label, width),
                        )
                        new_bar = self.page.locator(".w2-topbar").bounding_box()
                        new_rail = self.page.locator(".w2-sidebar").bounding_box()
                        self.assertAlmostEqual(
                            new_bar["height"], ref_bar["height"], delta=2,
                        )
                        self.assertAlmostEqual(
                            new_rail["width"], ref_rail["width"], delta=8,
                        )
                        filename = "phase1-%s-%s.png" % (label, width)
                        target = (
                            output_dir if save_baselines else Path(temporary)
                        ) / filename
                        target.parent.mkdir(parents=True, exist_ok=True)
                        self.page.screenshot(path=str(target), full_page=True)
                        self.assertTrue(target.is_file())
                reference.close()

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
