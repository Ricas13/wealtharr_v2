"""Phase 6 desktop/mobile owner Overview and recorded-vs-certified History."""
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
import os

from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from browser_tests.test_owner_journeys import BrowserHarness
from tracker.models import (
    Account, LedgerEntry, StrategyInstance, StrategyPreset, StrategyValueSnapshot,
)
from tracker.services import validate_and_record


@override_settings(TURNSTILE_REQUIRED=False)
class Phase6HistoryBrowserTests(BrowserHarness):
    def setUp(self):
        super().setUp()
        self.now = timezone.localdate()
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name":"9Sig","risk":"HIGH","summary":"Fixed","active":True},
        )
        account=Account.objects.create(
            owner=self.owner,name="Real cash ISA",kind="ISA",
            country="GB",broker="TRADING212",
        )
        self.cash=StrategyInstance.objects.create(
            owner=self.owner,account=account,preset=preset,
            name="Owner cash 9Sig",equity_symbol="LQQ3.L",
            safe_symbol="ERNS.L",tracking_only=True,
        )
        other=Account.objects.create(
            owner=self.other,name="Other private ISA",kind="ISA",
            broker="TRADING212",country="GB",
        )
        StrategyInstance.objects.create(
            owner=self.other,account=other,preset=preset,
            name="SECRET_UNRELATED_PORTFOLIO",
        )
        start=self.now-timedelta(days=120)
        validate_and_record(self.cash,LedgerEntry(
            kind="DEPOSIT",amount=D("1000"),occurred_on=start,
        ))
        StrategyValueSnapshot.objects.create(
            strategy=self.cash,as_of=self.now-timedelta(days=20),
            total_value=D("1000"),net_contributed=D("1000"),
        )
        acct2=Account.objects.create(
            owner=self.owner,name="Invested GIA",kind="GIA",
            broker="TRADING212",country="GB",
        )
        self.invested=StrategyInstance.objects.create(
            owner=self.owner,account=acct2,preset=preset,
            name="Invested GIA 9Sig",equity_symbol="LQQ3.L",
            safe_symbol="ERNS.L",tracking_only=True,
        )
        validate_and_record(self.invested,LedgerEntry(
            kind="DEPOSIT",amount=D("500"),occurred_on=start,
        ))
        validate_and_record(self.invested,LedgerEntry(
            kind="BUY",symbol="LQQ3.L",quantity=D("2"),
            unit_price=D("100"),occurred_on=start+timedelta(days=1),
        ))
        StrategyValueSnapshot.objects.create(
            strategy=self.invested,as_of=self.now-timedelta(days=3),
            total_value=D("595"),net_contributed=D("500"),
        )

    def test_overview_and_history_390_1440_with_no_cross_owner_metrics(self):
        output=Path(settings.BASE_DIR)/"docs/design/screenshots"
        keep=os.getenv("WEALTHARR_CAPTURE_V2_BASELINES")=="1"
        with TemporaryDirectory() as temp:
            for width in (390,1440):
                with self.subTest(width=width):
                    self.new_page(width)
                    self.sign_in()
                    self.visit("v2_overview")
                    self.page.get_by_role(
                        "heading",name="Your strategy accounts"
                    ).wait_for()
                    self.assertNotIn("SECRET_UNRELATED_PORTFOLIO",self.page.content())
                    self.page.get_by_text(
                        "Withheld: some accounts lack a same-date verified valuation"
                    ).wait_for()
                    self.assertLessEqual(
                        self.page.evaluate("document.documentElement.scrollWidth"),width+1,
                    )
                    target=(output if keep else Path(temp))/f"phase6-overview-{width}.png"
                    target.parent.mkdir(parents=True,exist_ok=True)
                    self.page.screenshot(path=str(target),full_page=True)
                    self.assertTrue(target.is_file())
                    self.visit("v2_instance_page",self.invested.pk,"history")
                    self.page.get_by_role(
                        "heading",name="Recorded investment history"
                    ).wait_for()
                    self.page.get_by_text(
                        "Recorded values — uncertified investment returns"
                    ).wait_for()
                    self.page.locator('svg[data-w2-history-chart]').wait_for()
                    self.assertNotIn("SECRET_UNRELATED_PORTFOLIO",self.page.content())
                    target=(output if keep else Path(temp))/f"phase6-history-invested-{width}.png"
                    self.page.screenshot(path=str(target),full_page=True)
                    self.assertTrue(target.is_file())
                    self.visit("v2_instance_page",self.cash.pk,"history")
                    self.page.get_by_text("Ledger-backed cash endpoint").wait_for()
                    self.page.get_by_role("heading",name="Source-labelled observation log").wait_for()
                    self.assertLessEqual(
                        self.page.evaluate("document.documentElement.scrollWidth"),width+1,
                    )
                    target=(output if keep else Path(temp))/f"phase6-history-cash-{width}.png"
                    self.page.screenshot(path=str(target),full_page=True)
                    self.assertTrue(target.is_file())
                    self.assertEqual(self.browser_errors, [])

    def test_history_range_selector_preserves_no_synthetic_returns(self):
        self.new_page(390)
        self.sign_in()
        self.visit("v2_instance_page",self.invested.pk,"history")
        self.page.locator('select[name="range"]').select_option("1y")
        self.page.get_by_role("heading",name="Recorded investment history").wait_for()
        self.assertIn("range=1y",self.page.url)
        self.assertIn("Not certified",self.page.content())
        self.assertEqual(self.browser_errors, [])
