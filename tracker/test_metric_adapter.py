"""Django orchestration for Section D cash-flow metrics; no signal logic changes."""
from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase

from tracker.cockpit import _performance
from tracker.models import (
    Account, LedgerEntry, MarketObservation, PriceMark,
    StrategyInstance, StrategyPreset,
)
from tracker.services import ledger_summary, validate_and_record


class PortfolioMetricAdapterTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="metric_owner", password="test-only-dummy-password")
        self.account = Account.objects.create(
            owner=self.owner, name="Metric ISA", kind="ISA", broker="TRADING212")
        preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "9Sig", "summary": "", "risk": "HIGH"})
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=self.account, preset=preset,
            name="Metric Portfolio", equity_symbol="LQQ3.L",
            safe_symbol="ERNS.L")
        self.start = date(2025, 1, 1)
        self.close = date(2026, 6, 30)
        validate_and_record(self.strategy, LedgerEntry(
            kind="DEPOSIT", occurred_on=self.start, amount=D("1000")))
        validate_and_record(self.strategy, LedgerEntry(
            kind="BUY", occurred_on=self.start,
            symbol="LQQ3.L", quantity=D("5"), unit_price=D("100")))
        validate_and_record(self.strategy, LedgerEntry(
            kind="BUY", occurred_on=self.start,
            symbol="ERNS.L", quantity=D("10"), unit_price=D("50")))
        for symbol, price in (("LQQ3.L", "120"), ("ERNS.L", "55")):
            MarketObservation.objects.create(
                symbol=symbol, as_of=self.close,
                price=D(price), currency="GBP")
            PriceMark.objects.create(strategy=self.strategy, symbol=symbol,
                price=D(price), as_of=self.close, source="yahoo")

    def metrics(self):
        return _performance(self.strategy, ledger_summary(self.strategy))

    def test_single_real_funding_has_simple_cumulative_and_cash_gain(self):
        result = self.metrics()
        self.assertEqual(result["gain_gbp"], D("150.00"))
        self.assertEqual(result["net_contributed_gbp"], D("1000.00"))
        self.assertEqual(result["cumulative"], D("15.00"))
        self.assertIsNotNone(result["xirr"])
        self.assertIsNone(result["twr"])

    def test_multiple_irregular_real_funding_rows_not_fake_cumulative(self):
        validate_and_record(self.strategy, LedgerEntry(
            kind="DEPOSIT", occurred_on=date(2025, 3, 1), amount=D("200")))
        result = self.metrics()
        self.assertEqual(result["gain_gbp"], D("150.00"))
        self.assertEqual(result["net_contributed_gbp"], D("1200.00"))
        self.assertIsNotNone(result["xirr"])
        self.assertIsNone(result["cumulative"])
        self.assertIsNone(result["twr"])

    def test_unverified_end_date_refuses_returns_and_gain(self):
        safe_mark = self.strategy.prices.get(symbol="ERNS.L")
        safe_mark.as_of -= timedelta(days=1)
        safe_mark.save(update_fields=["as_of"])
        result = self.metrics()
        self.assertIsNone(result["gain_gbp"])
        self.assertIsNone(result["xirr"])
        self.assertIsNone(result["cumulative"])

    def test_owner_specific_ledger_isolation(self):
        other = get_user_model().objects.create_user(
            username="other_metric_owner", password="test-only-dummy-password")
        other_account = Account.objects.create(owner=other, name="Other ISA",
                                               kind="ISA")
        other_strategy = StrategyInstance.objects.create(
            owner=other, account=other_account,
            preset=self.strategy.preset, name="Unrelated",
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L")
        validate_and_record(other_strategy, LedgerEntry(
            kind="DEPOSIT", occurred_on=self.start, amount=D("9999")))
        result = self.metrics()
        self.assertEqual(result["net_contributed_gbp"], D("1000.00"))
