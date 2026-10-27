"""Quick search opens from the keyboard, filters, moves and navigates."""
from django.test import override_settings

from browser_tests.harness import BrowserHarness
from tracker.models import Account, StrategyInstance


@override_settings(TURNSTILE_REQUIRED=False)
class QuickSearchBrowserTests(BrowserHarness):
    def setUp(self):
        super().setUp()
        account = Account.objects.create(
            owner=self.owner, name="Palette ISA", kind="ISA", broker="TRADING212", country="GB")
        self.instance = StrategyInstance.objects.create(
            owner=self.owner, account=account, preset=self.preset, name="Palette strategy")

    def test_slash_opens_filters_and_enter_navigates(self):
        self.new_page(1440)
        self.sign_in()
        self.visit("v2_overview")
        palette = self.page.locator("#v2-palette")
        self.assertFalse(palette.is_visible())
        self.page.keyboard.press("/")
        palette.wait_for(state="visible")
        self.assertEqual(self.page.evaluate("document.activeElement.id"), "v2-palette-input")
        self.page.keyboard.type("palette ledger")
        rows = self.page.locator("#v2-palette-list [role=option]")
        self.assertEqual(rows.count(), 1)
        self.assertIn("Ledger", rows.first.inner_text())
        self.page.keyboard.press("Enter")
        self.page.wait_for_url("**/ledger/**")
        self.assertIn("/ledger", self.page.url)

    def test_arrows_move_escape_closes_and_typing_in_a_field_does_not_open_it(self):
        self.new_page(390)
        self.sign_in()
        self.visit("v2_overview")
        self.page.keyboard.press("Control+k")
        self.page.locator("#v2-palette").wait_for(state="visible")
        self.page.keyboard.press("ArrowDown")
        self.assertEqual(
            self.page.locator("#v2-palette-list [aria-selected=true]").count(), 1)
        self.page.keyboard.press("Escape")
        self.assertFalse(self.page.locator("#v2-palette").is_visible())
        self.visit("v2_settings")
        field = self.page.locator("input[type=text], input[type=email]").first
        if field.count():
            field.focus()
            self.page.keyboard.type("a/b")
            self.assertFalse(self.page.locator("#v2-palette").is_visible())

    def test_the_service_worker_registers_and_stores_no_pages(self):
        self.new_page(1440)
        self.sign_in()
        self.visit("v2_overview")
        self.page.wait_for_function(
            "navigator.serviceWorker.getRegistration().then(r => !!(r && r.active))",
            timeout=15000)
        cached = self.page.evaluate("""async () => {
            const urls = [];
            for (const name of await caches.keys()) {
              for (const request of await (await caches.open(name)).keys()) urls.push(new URL(request.url).pathname);
            }
            return urls;
        }""")
        self.assertTrue(all(p.startswith("/static/") or p == "/offline/" for p in cached), cached)
