"""Account-aware mapping, real ledger handling, and monitoring lock."""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.models import Account, LedgerEntry, StrategyInstance, StrategyPreset
from tracker.core.catalogue import CATALOGUE_VERSION, onboarding_manifest, released_version
from tracker.instrument_mapping import (
    resolve_instruments, for_strategy, InstrumentMappingUnavailable)


class InstrumentMappingWorkflowTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            "instrument-owner", password="very-long-test-password")
        self.account = Account.objects.create(
            owner=self.owner, kind="ISA", name="Trading 212 live",
            country="GB", broker="TRADING212")
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=self.account,
            preset=StrategyPreset.objects.get(slug="nasdaq-quarterly-target"),
            name="9Sig live", equity_symbol="LQQ3.L", safe_symbol="ERNS.L",
            market_data_enabled=True)
        self.client.force_login(self.owner)
        self.day = timezone.localdate() - timedelta(days=3)
        self.strategy.ledger.create(
            kind=LedgerEntry.DEPOSIT, occurred_on=self.day, amount=D("1000"))
        self.url = reverse("v2_ledger_add", args=[self.strategy.pk])

    def test_mapping_is_deterministic_for_isa_and_gia(self):
        isa = resolve_instruments("nasdaq-quarterly-target", "GB", "ISA", "TRADING212")
        gia = resolve_instruments("nasdaq-quarterly-target", "GB", "GIA", "TRADING212")
        self.assertEqual(isa.equity, gia.equity)
        self.assertEqual(isa.safe, gia.safe)
        self.assertEqual(isa.safe.yahoo_ticker, "ERNS.L")
        with self.assertRaises(InstrumentMappingUnavailable):
            resolve_instruments("nasdaq-quarterly-target", "US", "ISA", "TRADING212")
        with self.assertRaises(InstrumentMappingUnavailable):
            resolve_instruments("nasdaq-quarterly-target", "GB", "SIPP", "TRADING212")
        with self.assertRaises(InstrumentMappingUnavailable):
            resolve_instruments("nasdaq-quarterly-target", "GB", "ISA", "UNKNOWN")

    def pin_for_tracking_ledger(self):
        """Use the owner-confirmed v2 form without inventing broker permissions."""
        StrategyInstance.objects.filter(pk=self.strategy.pk).update(
            strategy_key="nasdaq-quarterly-target",
            platform_code="TRADING212", account_type_code="ISA",
            started_on=self.day, owner_checked_on=timezone.localdate(),
            strategy_version=released_version("nasdaq-quarterly-target"),
            mapping_version=CATALOGUE_VERSION,
            resolved_instruments=onboarding_manifest(
                "nasdaq-quarterly-target", "TRADING212", "ISA"),
            tracking_only=True,
        )
        self.strategy.refresh_from_db()

    def test_broker_tickers_shown_as_dropdown_and_canonical_fill_recorded(self):
        self.pin_for_tracking_ledger()
        detail = self.client.get(reverse(
            "v2_instance_page", args=[self.strategy.pk, "strategy"]))
        self.assertContains(detail, "IE00BLRPRL42")
        self.assertContains(detail, "ERNS")
        add_page = self.client.get(self.url)
        self.assertEqual(add_page.status_code, 200)
        self.assertContains(add_page, "LQQ3.L")
        data = {
            "intent": "preview", "kind": "BUY", "occurred_on": str(self.day),
            "symbol": "LQQ3.L", "quantity": "1", "unit_price": "300",
            "fee": "0", "amount": "",
        }
        preview = self.client.post(self.url, data)
        self.assertContains(preview, "Impact preview")
        response = self.client.post(self.url, {"intent": "confirm"})
        self.assertRedirects(response, reverse("v2_instance_page", args=[
            self.strategy.pk, "ledger"]))
        self.assertEqual(self.strategy.ledger.filter(
            kind=LedgerEntry.BUY, symbol="LQQ3.L").count(), 1)

    def test_posted_unknown_ticker_is_rejected_even_if_valid_syntax(self):
        self.pin_for_tracking_ledger()
        data = {
            "intent": "preview", "kind": "BUY", "occurred_on": str(self.day),
            "symbol": "TQQQ", "quantity": "1", "unit_price": "30",
            "fee": "0", "amount": "",
        }
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose one exact mapped trading line")
        self.assertEqual(self.strategy.ledger.filter(kind=LedgerEntry.BUY).count(), 0)

    def test_market_monitoring_cannot_swap_mapped_tickers(self):
        # The retired mutable instrument/automation endpoint is no longer
        # writable. Saving per-instance v2 alert routes cannot swap tickers.
        url = "/app/strategies/%s/monitor/" % self.strategy.pk
        response = self.client.post(url, {
            "equity_symbol": "TQQQ", "safe_symbol": "QQQ3.L",
            "market_data_enabled": "on",
        })
        self.assertEqual(response.status_code, 404)
        response = self.client.post(reverse("v2_settings"), {
            "form_kind": "strategy", "strategy_id": self.strategy.pk,
            "equity_symbol": "TQQQ", "safe_symbol": "QQQ3.L",
            "route_quarterly_email": "on",
        })
        self.assertRedirects(response, reverse("v2_settings"))
        self.strategy.refresh_from_db()
        self.assertEqual(self.strategy.equity_symbol, "LQQ3.L")
        self.assertEqual(self.strategy.safe_symbol, "ERNS.L")
        self.assertTrue(self.strategy.market_data_enabled)
        self.assertTrue(self.strategy.alert_routes["quarterly"]["email"])

    def test_unmapped_existing_account_does_not_claim_a_pair(self):
        self.account.broker = "UNKNOWN"
        self.account.save(update_fields=["broker"])
        self.assertIsNone(for_strategy(self.strategy))

    def test_legacy_nonmapped_strategy_is_not_reinterpreted(self):
        self.strategy.equity_symbol = "3QQQ.DE"
        self.strategy.save(update_fields=["equity_symbol"])
        self.assertIsNone(for_strategy(self.strategy))

    def test_gia_owner_uses_same_verified_gbp_line_without_entering_tickers(self):
        from tracker.instrument_mapping import resolve_instruments
        gia = resolve_instruments(
            "nasdaq-quarterly-target", "GB", "GIA", "TRADING212")
        self.assertEqual(gia.equity.broker_ticker, "LQQ3")
        self.assertEqual(gia.safe.broker_ticker, "ERNS")
        self.assertEqual(gia.equity.currency, "GBP")
        self.assertEqual(gia.safe.currency, "GBP")
