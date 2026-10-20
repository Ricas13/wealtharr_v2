"""Real 1440px and 390px browser coverage for the Phase 3 ledger screens."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from datetime import timedelta

from browser_tests.test_owner_journeys import BrowserHarness
from tracker.models import Account, StrategyInstance, StrategyPreset
from tracker.core.catalogue import onboarding_manifest, released_version, CATALOGUE_VERSION


@override_settings(TURNSTILE_REQUIRED=False)
class Phase3LedgerBrowserTests(BrowserHarness):
    def setUp(self):
        super().setUp()
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "9Sig", "risk": "HIGH", "summary": "Fixed rules", "active": True},
        )
        account = Account.objects.create(
            owner=self.owner, name="Browser v2 ISA",
            kind="ISA", country="GB", broker="TRADING212",
        )
        self.instance = StrategyInstance.objects.create(
            owner=self.owner, account=account, preset=preset, name="Browser v2 9Sig",
            strategy_key="nasdaq-quarterly-target", platform_code="TRADING212",
            account_type_code="ISA", started_on=timezone.localdate()-timedelta(days=5),
            owner_checked_on=timezone.localdate(),
            strategy_version=released_version("nasdaq-quarterly-target"),
            mapping_version=CATALOGUE_VERSION,
            resolved_instruments=onboarding_manifest(
                "nasdaq-quarterly-target", "TRADING212", "ISA",
            ),
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L",
            tracking_only=True,
        )
        a2 = Account.objects.create(
            owner=self.other, name="Other owner's private ISA",
            kind="ISA", country="GB", broker="TRADING212",
        )
        StrategyInstance.objects.create(
            owner=self.other, account=a2, preset=preset,
            name="SECRET_OTHER_OWNER_BALANCES",
        )

    def test_ledger_add_preview_edit_delete_history_and_import_390_1440(self):
        output = Path(settings.BASE_DIR) / "docs/design/screenshots"
        save = os.getenv("WEALTHARR_CAPTURE_V2_BASELINES") == "1"
        with TemporaryDirectory() as tmp:
            for width in (1440, 390):
                with self.subTest(width=width):
                    self.new_page(width)
                    self.sign_in()

                    def snap(label):
                        self.assertLessEqual(
                            self.page.evaluate("document.documentElement.scrollWidth"),
                            width + 1,
                        )
                        self.assertNotIn(
                            "SECRET_OTHER_OWNER_BALANCES", self.page.content(),
                        )
                        target = (output if save else Path(tmp)) / (
                            f"phase3-{label}-{width}.png"
                        )
                        target.parent.mkdir(parents=True, exist_ok=True)
                        self.page.screenshot(path=str(target), full_page=True)
                        self.assertTrue(target.exists())

                    self.visit("v2_instance_page", self.instance.pk, "ledger")
                    self.page.get_by_role("link", name="Entries", exact=True).wait_for()
                    snap("ledger-entries")
                    self.page.get_by_role("link", name="Add entry").click()
                    self.page.locator('input[name="occurred_on"]').fill(
                        timezone.localdate().isoformat()
                    )
                    self.page.locator('input[name="amount"]').fill("600.00")
                    self.page.get_by_role("button", name="Preview impact").click()
                    self.page.get_by_role("heading", name="Impact preview", exact=False).wait_for()
                    snap("ledger-impact")
                    self.page.get_by_role("button", name="Confirm audited change").click()
                    self.page.get_by_text("600.00").first.wait_for()
                    self.assertNotIn("£14,820", self.page.content())
                    snap("ledger-added")
                    self.page.get_by_role("link", name="Edit", exact=True).first.click()
                    self.page.locator('input[name="amount"]').fill("500.00")
                    self.page.get_by_role("button", name="Preview impact").click()
                    self.page.get_by_role("button", name="Confirm audited change").click()
                    self.page.get_by_role("link", name="Change history").click()
                    self.page.get_by_role("heading", name="Immutable change history").wait_for()
                    snap("ledger-history")
                    self.page.get_by_role("link", name="Rebalances").click()
                    self.page.get_by_role("heading", name="Recorded quarterly rebalances").wait_for()
                    snap("ledger-rebalances")
                    self.visit("v2_ledger_import", self.instance.pk)
                    self.page.get_by_role(
                        "heading", name="Import original Trading 212 history"
                    ).wait_for()
                    snap("ledger-import")
                    self.assertEqual(self.browser_errors, [])
