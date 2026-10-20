"""Owner go-live: the single explicit step out of tracking-only mode."""
from datetime import date
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from tracker.core.catalogue import CATALOGUE_VERSION, onboarding_manifest, released_version
from tracker.instrument_mapping import for_strategy
from tracker.models import (
    Account, LedgerEntry, StrategyGoLive, StrategyInstance, StrategyPreset,
)

KEY = "nasdaq-quarterly-target"


class GoLiveTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="live-owner", password="secure-unique-live-password-789")
        self.client.force_login(self.owner)
        self.account = Account.objects.create(
            owner=self.owner, name="ISA", kind="ISA", country="GB", broker="TRADING212")
        self.preset = StrategyPreset.objects.get(slug=KEY)

    def legacy_linked(self, **changes):
        # Shape written by migration 0021 for an old cockpit strategy.
        fields = dict(
            owner=self.owner, account=self.account, preset=self.preset, name="9Sig",
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L", strategy_key=KEY,
            platform_code="TRADING212", account_type_code="ISA",
            onboarding_mode="legacy_link", tracking_only=True)
        fields.update(changes)
        strategy = StrategyInstance.objects.create(**fields)
        strategy.ledger.create(kind=LedgerEntry.DEPOSIT, amount=D("1000.00"),
                               occurred_on=date(2026, 10, 1))
        return strategy

    def onboarded(self):
        # Shape written by the v2 Add strategy wizard.
        return StrategyInstance.objects.create(
            owner=self.owner, account=self.account, preset=self.preset, name="9Sig v2",
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L", strategy_key=KEY,
            platform_code="TRADING212", account_type_code="ISA",
            started_on=date(2026, 10, 1), owner_checked_on=date(2026, 10, 1),
            strategy_version=released_version(KEY), mapping_version=CATALOGUE_VERSION,
            resolved_instruments=onboarding_manifest(KEY, "TRADING212", "ISA"),
            onboarding_mode="fresh", tracking_only=True, market_data_enabled=False)

    def post(self, strategy, **data):
        return self.client.post(reverse("v2_go_live", args=[strategy.pk]), data)

    def test_tracking_only_strategy_offers_go_live(self):
        strategy = self.legacy_linked()
        self.assertIsNone(for_strategy(strategy))
        page = self.client.get(reverse("v2_instance_page", args=[strategy.pk, "strategy"]))
        self.assertContains(page, 'id="go-live"')
        self.assertContains(page, reverse("v2_go_live", args=[strategy.pk]))
        today = self.client.get(reverse("v2_instance_page", args=[strategy.pk, "today"]))
        self.assertContains(today, "Not live yet")
        self.assertContains(today, "#go-live")

    def test_go_live_requires_post_and_confirmation(self):
        strategy = self.legacy_linked()
        self.assertEqual(self.client.get(reverse("v2_go_live", args=[strategy.pk])).status_code, 405)
        self.post(strategy)
        strategy.refresh_from_db()
        self.assertTrue(strategy.tracking_only)
        self.assertFalse(StrategyGoLive.objects.exists())

    def test_legacy_strategy_goes_live_and_pins_versions(self):
        strategy = self.legacy_linked()
        response = self.post(strategy, confirm="yes")
        self.assertRedirects(response, reverse("v2_instance_page", args=[strategy.pk, "today"]))
        strategy.refresh_from_db()
        self.assertFalse(strategy.tracking_only)
        self.assertTrue(strategy.market_data_enabled)
        self.assertEqual(strategy.strategy_version, released_version(KEY))
        self.assertEqual(strategy.mapping_version, CATALOGUE_VERSION)
        self.assertEqual(strategy.resolved_instruments, onboarding_manifest(KEY, "TRADING212", "ISA"))
        self.assertIsNotNone(strategy.owner_checked_on)
        self.assertIsNotNone(for_strategy(strategy))
        record = StrategyGoLive.objects.get(strategy=strategy)
        self.assertEqual(record.confirmed_by, self.owner)
        self.assertIn("LQQ3.L (IE00BLRPRL42)", record.attestation)
        self.assertIn("ERNS.L (IE00BCRY6441)", record.attestation)
        page = self.client.get(reverse("v2_instance_page", args=[strategy.pk, "strategy"]))
        self.assertContains(page, "Live since")
        self.assertContains(page, "Owner confirmed go-live")

    def test_onboarded_strategy_goes_live_without_repinning(self):
        strategy = self.onboarded()
        pinned = strategy.resolved_instruments
        self.post(strategy, confirm="yes")
        strategy.refresh_from_db()
        self.assertFalse(strategy.tracking_only)
        self.assertEqual(strategy.resolved_instruments, pinned)
        self.assertEqual(strategy.owner_checked_on, date(2026, 10, 1))
        self.assertIsNotNone(for_strategy(strategy))

    def test_hourly_market_sync_now_includes_the_strategy(self):
        strategy = self.legacy_linked()
        selected = StrategyInstance.objects.filter(
            market_data_enabled=True, tracking_only=False, preset__slug=KEY)
        self.assertFalse(selected.filter(pk=strategy.pk).exists())
        self.post(strategy, confirm="yes")
        self.assertTrue(selected.filter(pk=strategy.pk).exists())

    def test_go_live_is_once_only(self):
        strategy = self.legacy_linked()
        self.post(strategy, confirm="yes")
        self.post(strategy, confirm="yes")
        self.assertEqual(StrategyGoLive.objects.filter(strategy=strategy).count(), 1)

    def test_wrong_trading_line_is_refused_and_nothing_changes(self):
        strategy = self.legacy_linked(equity_symbol="QQQ3.L")
        self.post(strategy, confirm="yes")
        strategy.refresh_from_db()
        self.assertTrue(strategy.tracking_only)
        self.assertFalse(strategy.market_data_enabled)
        self.assertEqual(strategy.strategy_version, "")
        self.assertFalse(StrategyGoLive.objects.exists())

    def test_older_pinned_rule_version_is_refused(self):
        strategy = self.legacy_linked(strategy_version="nine_sig.v1.0.0")
        self.post(strategy, confirm="yes")
        strategy.refresh_from_db()
        self.assertTrue(strategy.tracking_only)
        self.assertFalse(StrategyGoLive.objects.exists())

    def test_another_owner_cannot_go_live(self):
        other = get_user_model().objects.create_user(
            username="intruder", password="another-long-unique-password-1")
        strategy = self.legacy_linked()
        self.client.force_login(other)
        self.assertEqual(self.post(strategy, confirm="yes").status_code, 404)
        strategy.refresh_from_db()
        self.assertTrue(strategy.tracking_only)
