"""Live Django+Chromium smoke for v2 Today and manual actual-fill pages."""
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
import os

from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from browser_tests.test_owner_journeys import BrowserHarness
from tracker.market_signals import quarter_end_before
from tracker.models import (
    Account, LedgerEntry, MarketObservation, PriceMark,
    StrategyInstance, StrategyPreset, StrategySignal, SignalFill,
)
from tracker.services import validate_and_record


@override_settings(TURNSTILE_REQUIRED=False)
class Phase4TodayBrowserTests(BrowserHarness):
    def setUp(self):
        super().setUp()
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "9Sig", "risk": "HIGH", "summary": "Fixed rules", "active": True},
        )
        account = Account.objects.create(
            owner=self.owner, name="Actual phase4 ISA", kind="ISA",
            country="GB", broker="TRADING212",
        )
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=account, preset=preset,
            name="Visible verified owner 9Sig", equity_symbol="LQQ3.L",
            safe_symbol="ERNS.L", market_data_enabled=True,
        )
        hidden_account = Account.objects.create(
            owner=self.other, name="Not your ISA", kind="ISA",
            country="GB", broker="TRADING212",
        )
        StrategyInstance.objects.create(
            owner=self.other, account=hidden_account, preset=preset,
            name="HIDDEN_OTHER_PERSON_ACCOUNT",
        )
        quarter = quarter_end_before(timezone.localdate())
        for kind, amount, symbol, qty, price in (
            ("DEPOSIT", "1000", "", "0", "0"),
            ("BUY", "0", "LQQ3.L", "2", "100"),
            ("BUY", "0", "ERNS.L", "1", "50"),
        ):
            validate_and_record(self.strategy, LedgerEntry(
                kind=kind, amount=D(amount), symbol=symbol,
                quantity=D(qty), unit_price=D(price),
                occurred_on=quarter - timedelta(days=5),
            ))
        quote_day = timezone.localdate() - timedelta(days=2)
        for symbol, price in (("LQQ3.L", D("101")), ("ERNS.L", D("51"))):
            MarketObservation.objects.create(
                symbol=symbol, as_of=quote_day, price=price, currency="GBP"
            )
            PriceMark.objects.create(
                strategy=self.strategy, symbol=symbol, as_of=quote_day,
                price=price, source="yahoo",
            )
        self.signal = StrategySignal.objects.create(
            strategy=self.strategy, as_of=quarter, kind="QUARTERLY",
            title="Quarterly recorded review", signal_key="phase4-browser-01",
            message="Audited recommendation, not a broker order.",
            actions=[{"side": "BUY", "symbol": "LQQ3.L", "quantity": "1"}],
        )
        self.execution_date = (quarter + timedelta(days=1)).isoformat()

    def test_today_and_guided_review_at_mobile_and_desktop(self):
        captures = Path(settings.BASE_DIR) / "docs/design/screenshots"
        keep = os.environ.get("WEALTHARR_CAPTURE_V2_BASELINES") == "1"
        with TemporaryDirectory() as temp:
            for width in (1440, 390):
                with self.subTest(width=width):
                    self.new_page(width)
                    self.sign_in()
                    self.visit("v2_instance_page", self.strategy.pk, "today")
                    self.page.get_by_role(
                        "heading", name="Review the verified quarterly proposal"
                    ).wait_for()
                    self.assertNotIn("HIDDEN_OTHER_PERSON_ACCOUNT", self.page.content())
                    self.assertNotIn("£14,820", self.page.content())
                    self.assertLessEqual(
                        self.page.evaluate("document.documentElement.scrollWidth"),
                        width + 1,
                    )
                    for label in ("today", "guided-review"):
                        if label == "guided-review":
                            self.page.get_by_role(
                                "link", name="Review proposal / I did this"
                            ).click()
                            self.page.get_by_role(
                                "heading", name="Confirm only what you actually traded"
                            ).wait_for()
                        output = (captures if keep else Path(temp)) / f"phase4-{label}-{width}.png"
                        output.parent.mkdir(parents=True, exist_ok=True)
                        self.page.screenshot(path=str(output), full_page=True)
                        self.assertTrue(output.is_file())
                    self.assertEqual(self.browser_errors, [])

    def test_real_fill_requires_explicit_broker_confirmation(self):
        self.new_page(390)
        self.sign_in()
        self.visit("v2_guided_review", self.strategy.pk, self.signal.pk)
        self.page.locator('input[name="executed_on"]').fill(self.execution_date)
        self.page.locator('input[name="quantity"]').fill("1")
        self.page.locator('input[name="unit_price"]').fill("100")
        self.page.locator('input[name="fee"]').fill("0.50")
        self.assertFalse(
            self.page.get_by_role(
                "button", name="I did this · record actual fill"
            ).is_disabled()
        )
        self.page.locator('input[name="actual_confirmation"]').check()
        self.page.get_by_role(
            "button", name="I did this · record actual fill"
        ).click()
        self.page.get_by_text("All proposed units recorded").wait_for()
        self.assertEqual(SignalFill.objects.filter(strategy=self.strategy).count(), 1)
        self.page.get_by_role(
            "button", name="Verify and complete review"
        ).click()
        self.page.get_by_role(
            "heading", name="Today", level=1
        ).wait_for()
        self.assertNotIn("I did this · record actual fill", self.page.content())
        self.assertEqual(self.browser_errors, [])
