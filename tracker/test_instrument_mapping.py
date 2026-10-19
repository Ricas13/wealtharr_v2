"""Account-aware mapping, real ledger handling, and monitoring lock."""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.models import Account, LedgerEntry, StrategyInstance, StrategyPreset
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
        self.url = reverse("record_entry", args=[self.strategy.pk])

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

    def test_broker_tickers_shown_as_dropdown_and_canonical_fill_recorded(self):
        detail = self.client.get(reverse("strategy_detail", args=[self.strategy.pk]))
        self.assertContains(detail, "IE00BLRPRL42")
        self.assertContains(detail, "ERNS")
        # The account-specific trading selector now lives in the Ledger tab,
        # rather than cluttering the strategy Overview.
        ledger_tab = self.client.get(reverse("strategy_detail", args=[self.strategy.pk]) + "?tab=ledger")
        self.assertContains(ledger_tab, '<option value="LQQ3.L"', html=False)
        data = {
            "kind": "BUY", "occurred_on": str(self.day),
            "symbol": "LQQ3.L", "quantity": "1", "unit_price": "300",
            "fee": "0", "amount": "",
        }
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.strategy.ledger.filter(
            kind=LedgerEntry.BUY, symbol="LQQ3.L").count(), 1)

    def test_posted_unknown_ticker_is_rejected_even_if_valid_syntax(self):
        data = {
            "kind": "BUY", "occurred_on": str(self.day),
            "symbol": "TQQQ", "quantity": "1", "unit_price": "30",
            "fee": "0", "amount": "",
        }
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select one of the two 9Sig instruments")
        self.assertEqual(self.strategy.ledger.filter(kind=LedgerEntry.BUY).count(), 0)

    def test_market_monitoring_cannot_swap_mapped_tickers(self):
        url = reverse("save_automation", args=[self.strategy.pk])
        response = self.client.post(url, {
            "equity_symbol": "TQQQ", "safe_symbol": "QQQ3.L",
            "market_data_enabled": "on",
        })
        self.assertEqual(response.status_code, 302)
        self.strategy.refresh_from_db()
        self.assertEqual(self.strategy.equity_symbol, "LQQ3.L")
        self.assertEqual(self.strategy.safe_symbol, "ERNS.L")
        self.assertTrue(self.strategy.market_data_enabled)

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
