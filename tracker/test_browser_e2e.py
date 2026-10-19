"""Section J: real Chromium private-cockpit workflows (isolated synthetic CI DB).

Opt in with WEALTHARR_BROWSER_TESTS=1. The ordinary Django test job skips this
class without importing/installing Playwright; the separate CI browser job
installs a pinned version and executes real responsive/keyboard interactions.

All actors, trades and broker CSV bytes here are TEST FIXTURES, never real
market observations or evidence of trading access or strategy parity.
"""
import os
import re
import unittest
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from django.utils import timezone

from .market_signals import quarter_end_before
from .models import (
    Account, LedgerEntry, OwnerTOTP, RebalanceExecution, SignalFill,
    StrategyBaseline, StrategyInstance, StrategyPreset, StrategySignal,
)
from .services import encrypt_secret, validate_and_record
from .totp import current_code


@unittest.skipUnless(os.environ.get("WEALTHARR_BROWSER_TESTS") == "1",
                     "Section J browser suite runs only in pinned Chromium CI job")
class OwnerChromiumJourneys(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from playwright.sync_api import sync_playwright
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(
                headless=True, args=["--no-sandbox"])
        except BaseException:
            cls.playwright.stop()
            super().tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls.playwright.stop()
        finally:
            super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.password = "section-j-browser-owner-password"
        self.owner = get_user_model().objects.create_user(
            username="browser-owner", password=self.password)
        self.preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "Fixed Nasdaq quarterly target", "risk": "HIGH",
                      "summary": "Synthetic browser test", "active": True})
        self.context = self.browser.new_context(
            viewport={"width": 360, "height": 800},
            device_scale_factor=1, locale="en-GB", reduced_motion="reduce")
        self.page = self.context.new_page()
        self.browser_errors = []
        self.page.on("pageerror", lambda error: self.browser_errors.append(str(error)))
        self.page.set_default_timeout(10000)

    def tearDown(self):
        try:
            self.assertEqual(self.browser_errors, [],
                             "Unexpected browser JavaScript exception")
        finally:
            self.context.close()
            super().tearDown()

    def visit(self, url_name, *args):
        response = self.page.goto(self.live_server_url + reverse(url_name, args=args))
        self.assertIsNotNone(response)
        self.assertLess(response.status, 500)
        return response

    def sign_in(self, username="browser-owner", password=None):
        self.visit("login")
        self.page.locator('input[name="username"]').fill(username)
        self.page.locator('input[name="password"]').fill(password or self.password)
        self.page.get_by_role("button", name=re.compile("Sign in")).click()
        self.page.wait_for_url(re.compile(r"/cockpit/(?:start/|$|security/verify/)"))

    def seed_portfolio(self):
        account = Account.objects.create(
            owner=self.owner, name="Owner test ISA", kind="ISA",
            country="GB", broker="TRADING212")
        return StrategyInstance.objects.create(
            owner=self.owner, account=account, preset=self.preset,
            name="Synthetic owner portfolio", market_data_enabled=True,
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L")

    def assert_mobile_fits(self):
        dimensions = self.page.evaluate("""() => ({
            document: document.documentElement.scrollWidth,
            viewport: document.documentElement.clientWidth,
            content: document.querySelector('main')?.getBoundingClientRect().width || 0
        })""")
        self.assertLessEqual(
            dimensions["document"], dimensions["viewport"] + 2,
            "Page-wide horizontal overflow at 360px (tables may scroll inside their own region)")

    def test_mobile_and_desktop_real_cash_onboarding_and_ledger(self):
        self.sign_in()
        self.page.get_by_role("heading", name="Start tracking 9Sig").wait_for()
        self.assert_mobile_fits()
        # A £500 monthly plan is NOT a deposit. Create the account with no cash.
        self.page.locator('input[name="starting_cash"]').fill("")
        self.page.locator('input[name="start_date"]').fill("")
        self.page.get_by_role("button", name=re.compile("Create my private portfolio")).click()
        self.page.get_by_role("heading", name="Today", exact=True).wait_for()
        strategy = StrategyInstance.objects.get(owner=self.owner)
        self.assertEqual(strategy.ledger.count(), 0)
        self.assertEqual(strategy.monthly_target, D("500"))
        self.assertEqual(strategy.account.kind, "ISA")
        self.assertEqual(strategy.account.broker, "TRADING212")
        self.assertEqual(self.page.locator('[data-testid="next-action"]').count(), 1)
        self.assert_mobile_fits()
        # Mobile keyboard-first navigation and skip-to-content affordance.
        self.page.reload()
        self.page.keyboard.press("Tab")
        self.assertIn("Skip to main content",
                      self.page.evaluate("document.activeElement.textContent"))
        self.page.get_by_role("button", name="Open navigation").click()
        self.page.get_by_role("navigation", name="Primary").get_by_role(
            "link", name="Ledger").click()
        self.page.get_by_role("heading", name="Ledger").wait_for()
        self.assert_mobile_fits()
        # One actual posted deposit. We never infer it from the planned target.
        self.page.locator("details").filter(
            has=self.page.locator("summary", has_text="Record an actual cash deposit")).first.evaluate(
                "(node) => node.open = true")
        self.page.locator("#ledger-deposit-amount").fill("125.50")
        self.page.locator("#ledger-deposit-date").fill(
            (timezone.localdate() - timedelta(days=1)).isoformat())
        self.page.get_by_role("button", name="Record this deposit").click()
        self.page.get_by_role("heading", name="Ledger").wait_for()
        self.assertEqual(strategy.ledger.filter(kind="DEPOSIT").count(), 1)
        self.assertEqual(strategy.ledger.get(kind="DEPOSIT").amount, D("125.50"))
        for destination in ("cockpit_history", "cockpit_strategy", "cockpit_settings"):
            self.visit(destination)
            self.assert_mobile_fits()
            self.assertEqual(self.page.locator('nav[aria-label="Primary"] a[aria-current="page"]').count(), 1)
        self.page.set_viewport_size({"width": 1280, "height": 800})
        for destination in ("cockpit_today", "cockpit_ledger", "cockpit_strategy"):
            self.visit(destination)
            self.assert_mobile_fits()

    def test_stale_unverified_price_fails_closed_in_real_browser(self):
        strategy = self.seed_portfolio()
        when = timezone.localdate() - timedelta(days=20)
        validate_and_record(strategy, LedgerEntry(
            kind="DEPOSIT", amount=D("1000"), occurred_on=when))
        validate_and_record(strategy, LedgerEntry(
            kind="BUY", symbol="LQQ3.L", quantity=D("3"),
            unit_price=D("100"), occurred_on=when))
        # No MarketObservation or broker price is fabricated.
        self.sign_in()
        self.page.get_by_role("heading", name="Today", exact=True).wait_for()
        self.assertEqual(self.page.locator('[data-testid="portfolio-value"]').inner_text().strip(),
                         "Unavailable")
        self.assertEqual(self.page.locator('[data-testid="next-action"]').count(), 1)
        self.assertEqual(self.page.get_by_role(
            "link", name="Review and record what I did").count(), 0)
        self.assertEqual(RebalanceExecution.objects.count(), 0)
        self.assert_mobile_fits()

    def test_csv_preview_rejects_ambiguous_headers_without_ledger_writes(self):
        strategy = self.seed_portfolio()
        self.sign_in()
        self.visit("cockpit_csv")
        self.page.get_by_role("heading", name="Import Trading 212 history").wait_for()
        self.page.locator("#t212-preview-file").set_input_files({
            "name": "synthetic-untrusted.csv", "mimeType": "text/csv",
            "buffer": b"Action,Time,Time,ID\nDeposit,2026-05-01,2026-05-01,test-id\n",
        })
        self.page.get_by_role("button", name="Preview without importing").click()
        self.page.get_by_role("heading", name="Import Trading 212 history").wait_for()
        self.assertIn("duplicate", self.page.locator("body").inner_text().lower())
        self.assertEqual(self.page.locator('button[name="apply"]').count(), 0)
        self.assertEqual(strategy.ledger.count(), 0)
        self.assert_mobile_fits()

    def test_cross_owner_login_does_not_expose_first_owner_records(self):
        strategy = self.seed_portfolio()
        validate_and_record(strategy, LedgerEntry(
            kind="DEPOSIT", amount=D("9876.54"),
            occurred_on=timezone.localdate() - timedelta(days=7)))
        get_user_model().objects.create_user(
            username="other-browser-owner", password="another-long-browser-password")
        self.visit("cockpit_ledger")  # Unauthenticated route: login required.
        self.page.get_by_role("heading", name="Welcome back.").wait_for()
        self.sign_in("other-browser-owner", "another-long-browser-password")
        self.page.get_by_role("heading", name="Start tracking 9Sig").wait_for()
        self.assertNotIn("9,876", self.page.locator("body").inner_text())
        self.assertEqual(strategy.ledger.count(), 1)

    def test_browser_pending_second_factor_blocks_private_pages(self):
        strategy = self.seed_portfolio()
        seed = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        OwnerTOTP.objects.create(owner=self.owner,
                                 secret_ciphertext=encrypt_secret(seed),
                                 enabled_at=timezone.now())
        self.sign_in()
        self.page.get_by_role("heading", name="Verify your authenticator").wait_for()
        self.visit("cockpit_ledger")
        self.page.get_by_role("heading", name="Verify your authenticator").wait_for()
        self.page.locator('input[name="code"]').fill(current_code(seed))
        self.page.get_by_role("button", name="Verify and continue").click()
        self.page.get_by_role("heading", name="Today", exact=True).wait_for()
        self.visit("cockpit_ledger")
        self.page.get_by_role("heading", name="Ledger").wait_for()

    def test_actual_partial_fill_never_creates_unverified_reset(self):
        strategy = self.seed_portfolio()
        quarter = quarter_end_before(timezone.localdate())
        trade_day = quarter + timedelta(days=1)
        if trade_day >= timezone.localdate():
            self.skipTest("No completed post-quarter execution day.")
        opening_day = quarter - timedelta(days=10)
        for values in (
            {"kind": "DEPOSIT", "amount": D("1000")},
            {"kind": "BUY", "symbol": "LQQ3.L", "quantity": D("7"),
             "unit_price": D("100")},
            {"kind": "BUY", "symbol": "ERNS.L", "quantity": D("6"),
             "unit_price": D("50")},
        ):
            validate_and_record(strategy, LedgerEntry(
                occurred_on=opening_day, **values))
        signal = StrategySignal.objects.create(
            strategy=strategy, signal_key=f"browser-quarter:{quarter}",
            kind="QUARTERLY", as_of=quarter,
            title="Synthetic post-quarter reset review",
            message="Owner must record actual execution",
            reset_reason="Synthetic browser-only reset",
            actions=[
                {"side": "SELL", "symbol": "LQQ3.L", "quantity": "1",
                 "estimated_gbp": "100"},
                {"side": "BUY", "symbol": "ERNS.L", "quantity": "2",
                 "estimated_gbp": "100"},
            ])
        self.sign_in()
        self.visit("cockpit_rebalance", signal.pk)
        self.page.get_by_role("heading", name="Record what you actually traded").wait_for()
        first = self.page.locator(".ck-single-fill-form").first
        first.locator('input[name="executed_on"]').fill(trade_day.isoformat())
        first.locator('input[name="quantity"]').fill("0.4")
        first.locator('input[name="unit_price"]').fill("100")
        first.locator('input[name="fee"]').fill("0")
        first.get_by_role("button", name="Save this one actual fill").click()
        self.page.get_by_role("heading", name="Record what you actually traded").wait_for()
        self.assertEqual(SignalFill.objects.filter(signal=signal).count(), 1)
        self.assertEqual(strategy.ledger.filter(kind="SELL").count(), 1)
        self.assertEqual(RebalanceExecution.objects.filter(signal=signal).count(), 0)
        self.assertEqual(StrategyBaseline.objects.filter(reset_signal=signal).count(), 0)
        self.assert_mobile_fits()
