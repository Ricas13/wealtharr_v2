"""Browser proof that a blocked price is explained on mobile and desktop."""
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from browser_tests.harness import BrowserHarness
from tracker.models import Account, LedgerEntry, MarketObservation, PriceMark, StrategyInstance
from tracker.services import validate_and_record


class PriceHealthBrowserTests(BrowserHarness):
    def setUp(self):
        super().setUp()
        account = Account.objects.create(
            owner=self.owner, name="Live account", kind="ISA", broker="TRADING212", country="GB")
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=account, preset=self.preset,
            name="Live comparison", equity_symbol="LQQ3.L", safe_symbol="ERNS.L",
            market_data_enabled=True)
        on = timezone.localdate() - timedelta(days=40)
        validate_and_record(self.strategy, LedgerEntry(
            kind="DEPOSIT", amount=Decimal("1000"), occurred_on=on))
        validate_and_record(self.strategy, LedgerEntry(
            kind="BUY", symbol="LQQ3.L", quantity=Decimal("1"),
            unit_price=Decimal("100"), occurred_on=on))
        d = timezone.localdate() - timedelta(days=2)
        PriceMark.objects.create(strategy=self.strategy, symbol="LQQ3.L", price=Decimal("101"),
                                 as_of=d, source="yahoo")
        MarketObservation.objects.create(symbol="LQQ3.L", price=Decimal("101"),
                                         currency="GBP", source="yahoo", raw_unit_verified=True,
                                         raw_quote_unit="GBP", raw_close=Decimal("101"), as_of=d)

    def test_missing_second_fund_is_diagnostic_not_green_or_a_trade(self):
        for width in (360, 1440):
            with self.subTest(width=width):
                page = self.new_page(width)
                self.sign_in()
                page.goto(self.live_server_url + reverse(
                    "v2_instance_page", args=[self.strategy.pk, "today"]), wait_until="networkidle")
                self.assertTrue(page.get_by_text("Prices need attention").first.is_visible())
                self.assertTrue(page.locator("#price-health").is_visible())
                self.assertIn("ERNS.L", page.locator("#price-health").inner_text())
                self.assertIn("Missing", page.locator("#price-health").inner_text())
                self.assertFalse(page.get_by_text("Review the verified quarterly proposal").count())
                self.assert_reachable_mobile()
