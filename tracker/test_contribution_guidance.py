"""Financial regressions: new money is not a new scheduled rebalance."""
from datetime import timedelta
from decimal import Decimal as D
from types import SimpleNamespace as NS

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from tracker.contribution_guidance import new_money_guidance, uninvested_deposits
from tracker.models import Account, LedgerEntry, PriceMark, StrategyInstance, StrategyPreset
from tracker.services import ledger_summary, validate_and_record
from tracker.strategies.contributions import allocation
from tracker.strategies.registry import describe_strategy


class PureContributionPolicyTests(SimpleTestCase):
    def test_9sig_and_3sig_buy_safe_not_growth(self):
        for key in ("nasdaq-quarterly-target", "three-sig"):
            self.assertEqual(allocation(key, D("500")), {"safe": D("500")})

    def test_underweight_funds_only_without_selling(self):
        weights = {"stocks": D("0.6"), "bonds": D("0.4")}
        result = allocation("all-weather", D("500"), values={
            "stocks": D("800"), "bonds": D("200")}, weights=weights)
        self.assertEqual(sum(result.values()), D("500"))
        self.assertGreater(result["bonds"], result["stocks"])
        self.assertTrue(all(v >= 0 for v in result.values()))

    def test_balanced_funds_get_weighted_new_money(self):
        result = allocation("hfea", D("500"), values={
            "stocks3x": D("550"), "bonds3x": D("450")},
            weights={"stocks3x": D("0.55"), "bonds3x": D("0.45")})
        self.assertEqual(result, {"stocks3x": D("275.00"), "bonds3x": D("225.00")})

    def test_dynamic_choice_requires_known_month_end_signal(self):
        self.assertEqual(allocation("dual-momentum", D("500")), {})
        self.assertEqual(allocation("dual-momentum", D("500"), chosen="bonds"),
                         {"bonds": D("500")})
        self.assertEqual(allocation("nasdaq-trend-dca", D("500")), {})

    def test_invalid_weight_and_negative_deposit_fail_closed(self):
        self.assertEqual(allocation("all-weather", D("500"), weights={
            "stocks": D(".7"), "bonds": D(".2")}), {})
        self.assertEqual(allocation("hfea", D("-500")), {})
        self.assertEqual(allocation("hfea", D("NaN")), {})

    def test_partial_fills_reduce_outstanding_deposit_without_losing_remainder(self):
        seq = [
            NS(pk=1, kind="DEPOSIT", amount=D("1000")),
            NS(pk=2, kind="BUY", quantity=D("6"), unit_price=D("100"), fee=D("0")),
            NS(pk=3, kind="DEPOSIT", amount=D("500")),
            NS(pk=4, kind="BUY", quantity=D("2"), unit_price=D("100"), fee=D("1")),
            NS(pk=5, kind="SELL", quantity=D("1"), unit_price=D("100"), fee=D("0")),
            NS(pk=6, kind="WITHDRAW", amount=D("50")),
        ]
        self.assertEqual(uninvested_deposits(seq), (D("649"), 3))


class LiveContributionGuidanceTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            "monthly-invest-owner", password="test-only-wealtharr")
        account = Account.objects.create(
            owner=self.owner, name="Contributions ISA", kind="ISA",
            country="GB", broker="TRADING212")
        key = "nasdaq-quarterly-target"
        preset, _ = StrategyPreset.objects.get_or_create(
            slug=key, defaults={"name": "9Sig", "active": True})
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=account, preset=preset,
            strategy_key=key, name="9Sig contributions",
            strategy_version=describe_strategy(key).version,
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L",
            market_data_enabled=True, tracking_only=False)
        self.day = timezone.localdate() - timedelta(days=2)

    def record(self, kind, *, amount="0", symbol="", quantity="0", price="0"):
        return validate_and_record(self.strategy, LedgerEntry(
            occurred_on=self.day, kind=kind, amount=D(amount),
            symbol=symbol, quantity=D(quantity), unit_price=D(price)))

    def prices(self):
        for symbol, price in (("LQQ3.L", D("100")), ("ERNS.L", D("50"))):
            PriceMark.objects.create(
                strategy=self.strategy, symbol=symbol, price=price,
                as_of=self.day, source="yahoo")

    def test_existing_9sig_deposit_has_safe_fund_prompt_then_partial_fill(self):
        self.record("DEPOSIT", amount="1000")
        self.record("BUY", symbol="LQQ3.L", quantity="6", price="100")
        self.record("BUY", symbol="ERNS.L", quantity="8", price="50")
        self.record("DEPOSIT", amount="500")
        self.prices()
        first = new_money_guidance(self.strategy, ledger_summary(self.strategy), prices_ready=True)
        self.assertEqual(first["rows"][0]["symbol"], "ERNS.L")
        self.assertEqual(first["rows"][0]["amount"], D("500"))
        self.record("BUY", symbol="ERNS.L", quantity="4", price="50")
        remaining = new_money_guidance(self.strategy, ledger_summary(self.strategy), prices_ready=True)
        self.assertEqual(remaining["rows"][0]["amount"], D("300"))
        self.assertEqual(first["deposit_id"], remaining["deposit_id"])
        self.record("BUY", symbol="ERNS.L", quantity="6", price="50")
        self.assertIsNone(new_money_guidance(
            self.strategy, ledger_summary(self.strategy), prices_ready=True))

    def test_blocks_for_missing_or_held_prices_and_tracking_only(self):
        self.record("DEPOSIT", amount="1000")
        self.record("BUY", symbol="LQQ3.L", quantity="6", price="100")
        self.record("DEPOSIT", amount="500")
        self.assertIsNone(new_money_guidance(
            self.strategy, ledger_summary(self.strategy), prices_ready=True))
        self.prices()
        self.assertIsNone(new_money_guidance(
            self.strategy, ledger_summary(self.strategy), prices_ready=False))
        self.strategy.tracking_only = True
        self.assertIsNone(new_money_guidance(
            self.strategy, ledger_summary(self.strategy), prices_ready=True))

    def test_withdrawal_and_cash_from_sales_do_not_expand_deposit_budget(self):
        self.record("DEPOSIT", amount="1000")
        self.record("BUY", symbol="LQQ3.L", quantity="6", price="100")
        self.record("BUY", symbol="ERNS.L", quantity="8", price="50")
        self.record("DEPOSIT", amount="500")
        self.record("SELL", symbol="LQQ3.L", quantity="1", price="100")
        self.record("WITHDRAW", amount="100")
        self.prices()
        got = new_money_guidance(self.strategy, ledger_summary(self.strategy), prices_ready=True)
        self.assertEqual(got["amount"], D("400"))
