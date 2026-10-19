"""Real Chromium acceptance flows for the private owner cockpit (Section J).

Run with:
    python -m playwright install --with-deps chromium
    python manage.py test browser_tests --verbosity 2

Only deterministic synthetic fixtures; never an actual broker login or order.
The fixture has no USD-to-GBP conversion, guessed quotes or opening trades.
"""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from tracker.market_signals import quarter_end_before
from tracker.models import (
    Account, LedgerEntry, OwnerTOTP, RebalanceExecution, SignalFill,
    StrategyBaseline, StrategyInstance, StrategyPreset, StrategySignal,
)
from tracker.services import encrypt_secret, validate_and_record
from tracker.totp import current_code, generate_secret


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
            css: getComputedStyle(document.querySelector('.cockpit')).display
        })""")
        self.assertEqual(dimension["css"], "grid", "Real cockpit CSS did not load")
        self.assertLessEqual(
            dimension["document"], dimension["viewport"] + 1,
            "Entire page has horizontal overflow at %s px" % dimension["viewport"])
        self.assertEqual(self.browser_errors, [], "Browser JavaScript errors")


class OwnerCockpitBrowserTests(BrowserHarness):
    def setUp(self):
        super().setUp()
        self.quarter = quarter_end_before(timezone.localdate())
        self.funding_day = self.quarter - timedelta(days=10)
        self.fill_day = self.quarter + timedelta(days=1)
        self.account = Account.objects.create(
            owner=self.owner, name="Owner synthetic ISA", kind="ISA",
            broker="TRADING212", country="GB")
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=self.account, preset=self.preset,
            name="Only my 9Sig", market_data_enabled=True,
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L")
        for kwargs in (
            {"kind": "DEPOSIT", "amount": D("1000"), "occurred_on": self.funding_day},
            {"kind": "BUY", "symbol": "LQQ3.L", "quantity": D("7"),
             "unit_price": D("100"), "occurred_on": self.funding_day},
            {"kind": "BUY", "symbol": "ERNS.L", "quantity": D("6"),
             "unit_price": D("50"), "occurred_on": self.funding_day},
        ):
            validate_and_record(self.strategy, LedgerEntry(**kwargs))
        other_account = Account.objects.create(
            owner=self.other, name="Private other ISA", kind="ISA",
            broker="TRADING212", country="GB")
        self.other_strategy = StrategyInstance.objects.create(
            owner=self.other, account=other_account, preset=self.preset,
            name="UNRELATED_SECRET_OWNER", market_data_enabled=False,
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L")
        validate_and_record(self.other_strategy, LedgerEntry(
            kind="DEPOSIT", amount=D("98765.43"), occurred_on=self.funding_day))

    def test_real_browser_today_history_ledger_strategy_settings_no_overflow(self):
        for width in (360, 1280):
            with self.subTest(width=width):
                self.new_page(width)
                self.sign_in()
                for name, title in (
                    ("cockpit_today", "Today"),
                    ("cockpit_history", "History"),
                    ("cockpit_ledger", "Ledger"),
                    ("cockpit_strategy", "Strategy"),
                    ("cockpit_settings", "Notifications"),
                ):
                    self.visit(name)
                    self.page.get_by_role("heading", name=title, exact=False).first.wait_for()
                    self.assert_reachable_mobile()
                    self.assertNotIn("UNRELATED_SECRET_OWNER", self.page.content())
                    self.assertNotIn("98765.43", self.page.content())
                self.assertEqual(self.browser_errors, [])

    def test_mobile_keyboard_navigation_escape_and_main_skip(self):
        self.new_page(360)
        self.sign_in()
        self.visit("cockpit_today")
        skip = self.page.get_by_role("link", name="Skip to main content")
        skip.focus()
        skip.press("Enter")
        self.assertEqual(self.page.evaluate("document.activeElement.id"), "main")
        toggle = self.page.get_by_role("button", name="Open navigation")
        toggle.focus()
        toggle.press("Enter")
        self.assertEqual(toggle.get_attribute("aria-expanded"), "true")
        self.page.get_by_role("navigation", name="Primary").get_by_role(
            "link", name="History").click()
        self.page.get_by_role("heading", name="History", exact=True).wait_for()
        self.visit("cockpit_today")
        toggle = self.page.get_by_role("button", name="Open navigation")
        toggle.click()
        self.page.keyboard.press("Escape")
        self.assertEqual(toggle.get_attribute("aria-expanded"), "false")
        self.assert_reachable_mobile()

    def test_stale_data_is_unavailable_never_executable(self):
        self.new_page(360)
        self.sign_in()
        self.visit("cockpit_today")
        self.assertEqual(
            self.page.get_by_test_id("portfolio-value").inner_text().strip(),
            "Unavailable")
        self.page.get_by_test_id("next-action").get_by_text(
            "Check verified prices again", exact=True).wait_for()
        self.assertEqual(
            self.page.get_by_test_id("next-action").locator(
                "a[href*='rebalance']").count(), 0)
        self.assertNotIn("UNRELATED_SECRET_OWNER", self.page.content())
        self.assert_reachable_mobile()

    def test_actual_cash_deposit_from_ledger_keeps_private_audit(self):
        self.new_page(360)
        self.sign_in()
        self.visit("cockpit_ledger")
        self.page.get_by_text("Record an actual cash deposit", exact=True).click()
        self.page.locator("#ledger-deposit-amount").fill("125.00")
        self.page.locator("#ledger-deposit-date").fill(
            timezone.localdate().isoformat())
        self.page.get_by_role("button", name="Record this deposit").click()
        self.page.wait_for_load_state("networkidle")
        self.page.get_by_text("Deposit recorded.").wait_for()
        self.assertEqual(self.strategy.ledger.filter(kind="DEPOSIT").count(), 2)
        self.assertEqual(self.strategy.ledger.filter(
            kind="DEPOSIT", amount=D("125")).count(), 1)
        self.assertEqual(self.other_strategy.ledger.count(), 1)
        self.assert_reachable_mobile()

    def test_partial_owner_fill_in_browser_never_creates_reset(self):
        if self.fill_day >= timezone.localdate():
            self.skipTest("No post-quarter historical execution date yet.")
        signal = StrategySignal.objects.create(
            strategy=self.strategy, kind="QUARTERLY",
            as_of=self.quarter,
            signal_key="quarter:browser:%s" % self.quarter,
            title="Synthetic dated review",
            message="A fictional proposal fixture; no order was sent",
            reset_reason="eight-quarter reset for regression fixture",
            actions=[
                {"side": "SELL", "symbol": "LQQ3.L", "quantity": "1",
                 "estimated_gbp": "100"},
                {"side": "BUY", "symbol": "ERNS.L", "quantity": "2",
                 "estimated_gbp": "100"},
            ])
        self.new_page(360)
        self.sign_in()
        self.visit("cockpit_rebalance", signal.pk)
        self.page.get_by_role(
            "heading", name="Record what you actually traded").wait_for()
        first = self.page.locator("form.ck-single-fill-form").first
        first.locator("[name='executed_on']").fill(self.fill_day.isoformat())
        first.locator("[name='quantity']").fill("0.4")
        first.locator("[name='unit_price']").fill("100")
        first.locator("[name='fee']").fill("0")
        first.get_by_role("button", name="Save this one actual fill").click()
        self.page.wait_for_load_state("networkidle")
        self.page.get_by_text("Actual fill saved in the ledger", exact=False).wait_for()
        self.assertEqual(SignalFill.objects.filter(signal=signal).count(), 1)
        self.assertEqual(self.strategy.ledger.filter(kind="SELL").count(), 1)
        self.assertFalse(RebalanceExecution.objects.filter(signal=signal).exists())
        self.assertFalse(StrategyBaseline.objects.filter(reset_signal=signal).exists())
        self.assertEqual(
            self.page.get_by_role("button", name="Verify the post-fill portfolio and finish").count(), 0)
        self.visit("cockpit_today")
        self.page.get_by_text("Continue your recorded partial fills").first.wait_for()
        self.visit("cockpit_history")
        self.page.get_by_text("Partial: 1 actual fill(s) recorded").wait_for()
        self.assert_reachable_mobile()

    def test_other_owner_review_cannot_be_opened_or_changed(self):
        signal = StrategySignal.objects.create(
            strategy=self.other_strategy, kind="QUARTERLY", as_of=self.quarter,
            signal_key="quarter:private",
            title="OTHER_PRIVATE_REVIEW", message="Other owner text",
            actions=[{"side": "BUY", "symbol": "LQQ3.L",
                      "quantity": "1", "estimated_gbp": "100"}])
        self.new_page(360)
        self.sign_in()
        response = self.visit("cockpit_rebalance", signal.pk)
        self.assertEqual(response.status, 404)
        self.assertFalse(SignalFill.objects.filter(signal=signal).exists())

    def test_optional_totp_challenge_before_private_browsing(self):
        seed = generate_secret()
        OwnerTOTP.objects.create(
            owner=self.owner, secret_ciphertext=encrypt_secret(seed),
            enabled_at=timezone.now())
        self.new_page(360)
        self.sign_in()
        self.page.get_by_role(
            "heading", name="Verify your authenticator").wait_for()
        self.visit("cockpit_ledger")
        self.page.get_by_role(
            "heading", name="Verify your authenticator").wait_for()
        self.page.locator("#totp-code").fill(current_code(seed))
        self.page.get_by_role("button", name="Verify and continue").click()
        self.page.wait_for_load_state("networkidle")
        self.visit("cockpit_ledger")
        self.page.get_by_role("heading", name="Ledger", exact=True).wait_for()
        self.assert_reachable_mobile()


class OnboardingBrowserTests(BrowserHarness):
    def test_blank_opening_deposit_keeps_illustrative_plan_out_of_ledger(self):
        self.new_page(360)
        self.sign_in()
        self.page.get_by_role( "heading", name="Start tracking 9Sig").wait_for()
        self.page.get_by_label("How are you starting?").select_option("FRESH")
        self.page.get_by_role(
            "button", name="Create my private portfolio").click()
        self.page.wait_for_load_state("networkidle")
        self.page.get_by_role("heading", name="Today", exact=True).wait_for()
        strategy = StrategyInstance.objects.get(owner=self.owner)
        self.assertEqual(strategy.monthly_target, D("500"))
        self.assertFalse(strategy.ledger.exists(), "A plan is not real funding")
        self.assertEqual(strategy.account.kind, "ISA")
        self.assert_reachable_mobile()

    def test_actual_starting_cash_is_one_dated_deposit_not_fictional_trade(self):
        self.new_page(1280)
        self.sign_in()
        self.page.get_by_label("How are you starting?").select_option("FRESH")
        self.page.locator("[name='starting_cash']").fill("250.00")
        actual_date = timezone.localdate() - timedelta(days=2)
        self.page.locator("[name='start_date']").fill(actual_date.isoformat())
        self.page.get_by_role(
            "button", name="Create my private portfolio").click()
        self.page.wait_for_load_state("networkidle")
        self.page.get_by_role("heading", name="Today", exact=True).wait_for()
        strategy = StrategyInstance.objects.get(owner=self.owner)
        self.assertEqual(strategy.ledger.count(), 1)
        entry = strategy.ledger.get()
        self.assertEqual(entry.kind, "DEPOSIT")
        self.assertEqual(entry.amount, D("250.00"))
        self.assertEqual(entry.occurred_on, actual_date)
        self.assert_reachable_mobile()
