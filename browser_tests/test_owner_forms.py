"""Every owner form a person uses day to day, clicked in a real browser."""
from datetime import timedelta
from decimal import Decimal as D

from django.urls import reverse
from django.utils import timezone

from browser_tests.harness import BrowserHarness
from tracker.core.catalogue import CATALOGUE_VERSION, onboarding_manifest, released_version
from tracker.models import (
    Account, LedgerEntry, NotificationPreference, StrategyGoLive, StrategyInstance,
)

KEY = "nasdaq-quarterly-target"


class OwnerFormsBrowserTests(BrowserHarness):
    def strategy(self, **changes):
        account = Account.objects.create(owner=self.owner, name="ISA", kind="ISA",
                                         country="GB", broker="TRADING212")
        fields = dict(
            owner=self.owner, account=account, preset=self.preset, name="9Sig",
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L", strategy_key=KEY,
            platform_code="TRADING212", account_type_code="ISA",
            started_on=timezone.localdate() - timedelta(days=5),
            owner_checked_on=timezone.localdate(),
            strategy_version=released_version(KEY), mapping_version=CATALOGUE_VERSION,
            resolved_instruments=onboarding_manifest(KEY, "TRADING212", "ISA"),
            onboarding_mode="fresh", tracking_only=False, market_data_enabled=True)
        fields.update(changes)
        return StrategyInstance.objects.create(**fields)

    def go(self, strategy, section):
        self.visit("v2_instance_page", strategy.pk, section)

    def submit(self, form):
        with self.page.expect_navigation():
            form.get_by_role("button", name="Save").click()
        self.assertNotIn("Not Found", self.page.content())

    def test_dashboard_ledger_overview_and_alert_forms_save(self):
        strategy = self.strategy()
        self.new_page(1440)
        self.sign_in()
        self.go(strategy, "today")
        self.assertTrue(self.page.get_by_text("Add your first deposit").is_visible())
        # Add money on the Dashboard.
        money = self.page.locator("section[aria-labelledby=w2-add-money] form")
        money.locator("input[name=amount]").fill("1000")
        self.submit(money)
        self.assertTrue(self.page.get_by_text("Saved.").is_visible())
        self.assertTrue(strategy.ledger.filter(kind="DEPOSIT", amount=D("1000")).exists())
        self.assertTrue(self.page.get_by_text("Opening position from the rules").is_visible())
        # Record a trade on the Dashboard.
        trade = self.page.locator("section[aria-labelledby=w2-add-trade] form")
        trade.locator("input[name=quantity]").fill("10")
        trade.locator("input[name=unit_price]").fill("50")
        self.submit(trade)
        self.assertTrue(strategy.ledger.filter(kind="BUY", symbol="LQQ3.L", quantity=D("10")).exists())
        # Add money from the Ledger, then edit and delete it.
        self.go(strategy, "ledger")
        side = self.page.locator("aside.w2-side form").first
        side.locator("input[name=amount]").fill("25")
        self.submit(side)
        entry = strategy.ledger.get(amount=D("25"))
        self.page.goto(self.live_server_url + reverse("v2_ledger_modify", args=[strategy.pk, entry.pk, "edit"]),
                       wait_until="networkidle")
        self.assertEqual(self.page.locator("body").count(), 1)
        self.assertNotIn("Not Found", self.page.content())
        # Overview quick add.
        self.visit("v2_overview")
        quick = self.page.locator("#w2-quick-money")
        quick.locator("input[name=amount]").fill("5")
        self.submit(quick)
        self.assertTrue(strategy.ledger.filter(amount=D("5")).exists())
        # Alerts: owner destinations, then per-strategy routes.
        self.visit("v2_settings")
        owner_form = self.page.locator("form", has=self.page.locator("input[name=form_kind][value=owner]"))
        owner_form.locator("input[name=destination_email]").fill("me@example.invalid")
        owner_form.locator("input[name=email_enabled]").check()
        self.submit(owner_form)
        self.assertTrue(NotificationPreference.objects.get(owner=self.owner).email_enabled)
        self.go(strategy, "strategy")
        routes = self.page.locator("#alerts form")
        with self.page.expect_navigation():
            routes.get_by_role("button", name="Save alerts").click()
        self.assertIn("/strategy/", self.page.url)
        self.assertEqual(self.browser_errors, [])

    def test_half_live_strategy_is_finished_from_the_dashboard(self):
        strategy = self.strategy(started_on=None, onboarding_mode="legacy_link")
        StrategyGoLive.objects.create(strategy=strategy, confirmed_by=self.owner,
                                      strategy_version=strategy.strategy_version,
                                      mapping_version=CATALOGUE_VERSION, attestation="old")
        self.new_page(390)
        self.sign_in()
        self.go(strategy, "today")
        with self.page.expect_navigation():
            self.page.get_by_role("link", name="Finish setup").click()
        card = self.page.locator("#go-live")
        card.locator("input[name=confirm]").check()
        with self.page.expect_navigation():
            card.get_by_role("button", name="Finish setup").click()
        strategy.refresh_from_db()
        self.assertIsNotNone(strategy.started_on)
        self.assertTrue(self.page.get_by_text("Add your first deposit").is_visible())
        money = self.page.locator("section[aria-labelledby=w2-add-money] form")
        money.locator("input[name=amount]").fill("100")
        self.submit(money)
        self.assertTrue(strategy.ledger.filter(kind=LedgerEntry.DEPOSIT, amount=D("100")).exists())
        self.assertEqual(self.browser_errors, [])
