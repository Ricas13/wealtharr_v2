"""Phase 5 real-browser Breakdown checks: two widths, export and owner isolation."""
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
import os

from django.conf import settings
from django.test import override_settings

from browser_tests.test_owner_journeys import BrowserHarness
from tracker.models import (
    Account, LedgerEntry, StrategyInstance, StrategyPreset, StrategySignal,
)
from tracker.services import validate_and_record
from tracker.strategies.nine_sig.v2 import VERSION
from tracker.instrument_mapping import MAPPING_VERSION


@override_settings(TURNSTILE_REQUIRED=False)
class Phase5BreakdownBrowserTests(BrowserHarness):
    def setUp(self):
        super().setUp()
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "9Sig", "summary": "Fixed rules", "risk": "HIGH", "active": True},
        )
        account = Account.objects.create(
            owner=self.owner, name="Phase5 actual ISA",
            kind="ISA", broker="TRADING212", country="GB",
        )
        self.instance = StrategyInstance.objects.create(
            owner=self.owner, account=account, preset=preset,
            name="My saved 9Sig", strategy_key="nasdaq-quarterly-target",
            platform_code="TRADING212", account_type_code="ISA",
            strategy_version=VERSION, mapping_version=MAPPING_VERSION,
            started_on=date(2025, 2, 11), tracking_only=True,
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L",
        )
        outsider = Account.objects.create(
            owner=self.other, name="OTHER PRIVATE ISA", kind="ISA",
            broker="TRADING212", country="GB",
        )
        hidden = StrategyInstance.objects.create(
            owner=self.other, account=outsider, preset=preset,
            name="SECRET_OTHER_OWNER_PORTFOLIO",
        )
        StrategySignal.objects.create(
            strategy=hidden, signal_key="quarter:2025-06-30",
            kind="QUARTERLY", as_of=date(2025, 6, 30),
            title="OTHER_SECRET_QUARTER", message="Private owner data",
        )
        for kind, when, cash, qty, symbol, price in (
            ("DEPOSIT", date(2025, 2, 11), "1000", "0", "", "0"),
            ("BUY", date(2025, 2, 11), "0", "6", "LQQ3.L", "100"),
            ("DEPOSIT", date(2025, 4, 13), "100", "0", "", "0"),
            ("DEPOSIT", date(2025, 8, 20), "300", "0", "", "0"),
        ):
            validate_and_record(self.instance, LedgerEntry(
                kind=kind, occurred_on=when, amount=D(cash),
                quantity=D(qty), symbol=symbol, unit_price=D(price),
            ))
        self.first = StrategySignal.objects.create(
            strategy=self.instance, signal_key="quarter:2025-09-30",
            kind="QUARTERLY", as_of=date(2025, 9, 30),
            title="Saved Q3 audit", message="No invented live fills",
            actions=[{"side": "BUY", "symbol": "LQQ3.L", "quantity": "1.5", "estimated_gbp": "150"}],
        )
        self.second = StrategySignal.objects.create(
            strategy=self.instance, signal_key="quarter:2025-06-30",
            kind="QUARTERLY", as_of=date(2025, 6, 30),
            title="Saved Q2 audit", message="Read-only earlier period",
        )

    def test_read_only_breakdown_390_and_1440(self):
        screenshots = Path(settings.BASE_DIR) / "docs/design/screenshots"
        keep = os.environ.get("WEALTHARR_CAPTURE_V2_BASELINES") == "1"
        with TemporaryDirectory() as temp:
            for width in (390, 1440):
                with self.subTest(width=width):
                    self.new_page(width)
                    self.sign_in()
                    self.visit("v2_instance_page", self.instance.pk, "breakdown")
                    self.page.get_by_role(
                        "heading", name="Quarterly decision evidence"
                    ).wait_for()
                    self.page.get_by_role("heading", name="Saved Q3 audit").wait_for()
                    self.page.get_by_text("981.52").first.wait_for()
                    self.assertNotIn("SECRET_OTHER_OWNER_PORTFOLIO", self.page.content())
                    self.assertNotIn("OTHER_SECRET_QUARTER", self.page.content())
                    self.assertNotIn("£14,820", self.page.content())
                    self.assertLessEqual(
                        self.page.evaluate("document.documentElement.scrollWidth"),
                        width + 1,
                    )
                    self.page.get_by_text("Growth drawdown · information only").wait_for()
                    path = (screenshots if keep else Path(temp)) / f"phase5-breakdown-{width}.png"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    self.page.screenshot(path=str(path), full_page=True)
                    self.assertTrue(path.exists())
                    self.page.get_by_label("Saved review quarter").select_option("2025-06-30")
                    self.page.get_by_role("button", name="Inspect review").click()
                    self.page.get_by_role("heading", name="Quarterly decision evidence").wait_for()
                    self.assertIn("quarter=2025-06-30", self.page.url)
                    self.page.get_by_role("heading", name="Saved Q2 audit").wait_for()
                    self.assertEqual(self.browser_errors, [])

    def test_export_audit_with_zero_side_effects(self):
        self.new_page(1440)
        self.sign_in()
        self.visit("v2_instance_page", self.instance.pk, "breakdown")
        with self.page.expect_download() as download:
            self.page.get_by_role("link", name="Export audit CSV").click()
        self.assertTrue(download.value.suggested_filename.endswith(".csv"))
        self.assertEqual(self.instance.signals.count(), 2)
        self.assertEqual(self.instance.baselines.count(), 0)
        self.assertEqual(self.browser_errors, [])
