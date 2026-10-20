"""Phase 8: real login and the complete v2 owner journey at 1440/390.

Captures one real Django screenshot per page. Prototype comparison uses shared
chrome measurements; no screenshot claims fictional market/account data parity.
"""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from django.conf import settings
from django.test import override_settings
from browser_tests.harness import BrowserHarness
from tracker.models import Account, StrategyInstance, StrategyPreset


@override_settings(TURNSTILE_REQUIRED=False)
class Phase8OnlyV2BrowserTests(BrowserHarness):
    def setUp(self):
        super().setUp()
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name":"9Sig", "risk":"HIGH", "summary":"Fixed", "active":True},
        )
        account = Account.objects.create(
            owner=self.owner, name="Original Trading 212 ISA",
            broker="TRADING212", country="GB", kind="ISA",
        )
        self.instance = StrategyInstance.objects.create(
            owner=self.owner, account=account, preset=preset,
            name="Existing 9Sig", equity_symbol="LQQ3.L",
            safe_symbol="ERNS.L", tracking_only=True,
        )
        other = Account.objects.create(
            owner=self.other, name="Other ISA",
            broker="TRADING212", country="GB", kind="ISA",
        )
        StrategyInstance.objects.create(
            owner=self.other, account=other, preset=preset,
            name="SECRET_OTHER_OWNER",
        )

    def test_login_and_ten_owner_views_against_approved_chrome(self):
        pages = [
            ("overview","v2_overview",()),
            ("today","v2_instance_page",(self.instance.pk,"today")),
            ("history","v2_instance_page",(self.instance.pk,"history")),
            ("ledger","v2_instance_page",(self.instance.pk,"ledger")),
            ("strategy","v2_instance_page",(self.instance.pk,"strategy")),
            ("breakdown","v2_instance_page",(self.instance.pk,"breakdown")),
            ("add-strategy","v2_add_strategy",()),
            ("catalogue","v2_catalogue",()),
            ("settings","v2_settings",()),
        ]
        artifacts = Path(settings.BASE_DIR) / "docs/design/screenshots"
        keep = os.environ.get("WEALTHARR_CAPTURE_V2_BASELINES") == "1"
        with TemporaryDirectory() as temp:
            for width in (1440,390):
                with self.subTest(width=width):
                    self.new_page(width)
                    self.visit("login")
                    self.page.get_by_role("heading",name="Welcome back.").wait_for()
                    self.assertIn("css/v2.css",self.page.content())
                    folder = artifacts if keep else Path(temp)
                    folder.mkdir(parents=True,exist_ok=True)
                    login = folder / ("phase8-login-%s.png" % width)
                    self.page.screenshot(path=str(login),full_page=True)
                    self.assertTrue(login.exists())
                    self.sign_in()
                    self.assertIn(
                        "/s/%s/today/" % self.instance.pk, self.page.url
                    )
                    for label,name,args in pages:
                        self.visit(name,*args)
                        self.page.locator("#v2-main h1").first.wait_for()
                        self.assertNotIn("SECRET_OTHER_OWNER",self.page.content())
                        self.assertLessEqual(
                            self.page.evaluate("document.documentElement.scrollWidth"),
                            width+1,
                        )
                        self.assert_compact_chrome(width)
                        screenshot=folder/("phase8-%s-%s.png"%(label,width))
                        self.page.screenshot(path=str(screenshot),full_page=True)
                        self.assertTrue(screenshot.exists())
                    self.assertEqual(self.browser_errors, [])

    def test_legacy_bookmark_transparently_opens_v2(self):
        self.new_page(390)
        self.sign_in()
        self.page.goto(self.live_server_url+"/cockpit/history/",wait_until="networkidle")
        self.assertIn("/s/%s/history/"%self.instance.pk,self.page.url)
        self.assertIsNotNone(self.page.locator(".w2-sidebar").bounding_box())
        self.assertNotIn("SECRET_OTHER_OWNER",self.page.content())
