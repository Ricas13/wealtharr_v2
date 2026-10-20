"""Shared real Chromium/Django harness for v2-only owner journeys.

No retired cockpit routes or old UI assertions belong in this module.
"""
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from tracker.models import StrategyPreset

@override_settings(TURNSTILE_REQUIRED=False)
class BrowserHarness(StaticLiveServerTestCase):
    """Starts a real browser and Django static live server, no external hosts."""

    PASSWORD = "synthetic-browser-password-2026"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Deliberately import here: the regular 'manage.py test tracker'
        # does not need Playwright installed or a browser process.
        from playwright.sync_api import sync_playwright
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls.playwright.stop()
        finally:
            super().tearDownClass()

    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            "owner-browser", email="owner@example.invalid",
            password=self.PASSWORD)
        self.other = get_user_model().objects.create_user(
            "other-browser", password=self.PASSWORD)
        self.preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={
                "name": "9Sig", "risk": "HIGH", "summary": "Test rules",
                "active": True,
            })
        self.ctx = None
        self.page = None
        self.browser_errors = []

    def tearDown(self):
        if self.ctx:
            self.ctx.close()
        super().tearDown()

    def new_page(self, width=360):
        if self.ctx:
            self.ctx.close()
        self.ctx = self.browser.new_context(
            viewport={"width": width, "height": 800},
            device_scale_factor=1, reduced_motion="reduce")
        self.page = self.ctx.new_page()
        self.page.on("pageerror", lambda error: self.browser_errors.append(str(error)))
        return self.page

    def visit(self, name, *args):
        return self.page.goto(self.live_server_url + reverse(name, args=args),
                              wait_until="networkidle")

    def sign_in(self):
        self.visit("login")
        self.page.locator("#id_username").fill(self.owner.username)
        self.page.locator("#id_password").fill(self.PASSWORD)
        self.page.get_by_role("button", name="Sign in", exact=False).click()
        self.page.wait_for_load_state("networkidle")

    def assert_reachable_mobile(self):
        """A table can scroll within a container; the whole page cannot."""
        dimension = self.page.evaluate("""() => ({
            viewport: window.innerWidth,
            document: document.documentElement.scrollWidth,
            css: getComputedStyle(document.querySelector('.w2-shell')).display
        })""")
        self.assertEqual(dimension["css"], "grid", "Real v2 CSS did not load")
        self.assertLessEqual(
            dimension["document"], dimension["viewport"] + 1,
            "Entire page has horizontal overflow at %s px" % dimension["viewport"])
        self.assertEqual(self.browser_errors, [], "Browser JavaScript errors")


