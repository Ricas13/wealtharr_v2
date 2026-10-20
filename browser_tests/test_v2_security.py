"""Phase 8: live-browser private owner checks on the real v2 routes.

These replace the retired cockpit's browser-only assertions, without assuming
that a redirect, stale mark, or owner-provided cash is execution evidence.
"""
from datetime import timedelta
from decimal import Decimal
from django.test import override_settings
from django.utils import timezone

from browser_tests.harness import BrowserHarness
from tracker.models import Account, LedgerEntry, OwnerTOTP, StrategyInstance
from tracker.services import encrypt_secret, validate_and_record
from tracker.totp import current_code, generate_secret


@override_settings(TURNSTILE_REQUIRED=False)
class V2OwnerSecurityBrowserTests(BrowserHarness):
    def setUp(self):
        super().setUp()
        owner_account = Account.objects.create(
            owner=self.owner, name="My Trading 212 ISA", kind="ISA",
            broker="TRADING212", country="GB")
        foreign_account = Account.objects.create(
            owner=self.other, name="PRIVATE_FOREIGN_ACCOUNT", kind="ISA",
            broker="TRADING212", country="GB")
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=owner_account, preset=self.preset,
            name="Own 9Sig", equity_symbol="LQQ3.L", safe_symbol="ERNS.L",
            market_data_enabled=True)
        self.foreign = StrategyInstance.objects.create(
            owner=self.other, account=foreign_account, preset=self.preset,
            name="PRIVATE_FOREIGN_STRATEGY", equity_symbol="LQQ3.L",
            safe_symbol="ERNS.L")
        validate_and_record(self.foreign, LedgerEntry(
            kind="DEPOSIT", amount=Decimal("98765.43"),
            occurred_on=timezone.localdate()-timedelta(days=7)))

    def test_anonymous_owner_and_foreign_strategy_isolation_and_keyboard(self):
        self.new_page(390)
        for name, args in (
            ("v2_overview", ()),
            ("v2_settings", ()),
            ("v2_instance_page", (self.strategy.pk, "ledger")),
        ):
            response = self.visit(name, *args)
            self.assertIn("/login/", self.page.url)
            self.assertNotIn("PRIVATE_FOREIGN_ACCOUNT", self.page.content())
            self.assertNotIn("98765.43", self.page.content())
        self.sign_in()
        for section in ("today", "history", "ledger", "strategy", "breakdown"):
            response = self.visit("v2_instance_page", self.strategy.pk, section)
            self.assertEqual(response.status, 200)
            self.assertNotIn("PRIVATE_FOREIGN_STRATEGY", self.page.content())
            self.assertNotIn("98765.43", self.page.content())
            self.assert_reachable_mobile()
        foreign = self.visit("v2_instance_page", self.foreign.pk, "ledger")
        self.assertEqual(foreign.status, 404)
        self.visit("v2_instance_page", self.strategy.pk, "today")
        skip = self.page.get_by_role("link", name="Skip to main content")
        skip.focus()
        skip.press("Enter")
        self.assertEqual(self.page.evaluate("document.activeElement.id"), "v2-main")
        # Located by its controlled region: v2.js renames the button to
        # "Close navigation" while the drawer is open.
        toggle = self.page.locator('[aria-controls="v2-sidebar"]')
        toggle.click()
        self.assertEqual(toggle.get_attribute("aria-expanded"), "true")
        self.page.keyboard.press("Escape")
        self.assertEqual(toggle.get_attribute("aria-expanded"), "false")
        self.assert_reachable_mobile()

    def test_enabled_totp_blocks_v2_until_a_valid_code(self):
        seed = generate_secret()
        OwnerTOTP.objects.create(
            owner=self.owner, secret_ciphertext=encrypt_secret(seed),
            enabled_at=timezone.now())
        self.new_page(390)
        self.sign_in()
        self.page.get_by_role("heading", name="Verify your authenticator").wait_for()
        self.visit("v2_instance_page", self.strategy.pk, "ledger")
        self.page.get_by_role("heading", name="Verify your authenticator").wait_for()
        self.page.locator('input[name="code"]').fill(current_code(seed))
        self.page.get_by_role("button", name="Verify and continue").click()
        self.page.wait_for_load_state("networkidle")
        response = self.visit("v2_instance_page", self.strategy.pk, "ledger")
        self.assertEqual(response.status, 200)
        self.assertNotIn("PRIVATE_FOREIGN_STRATEGY", self.page.content())
        self.assert_reachable_mobile()
