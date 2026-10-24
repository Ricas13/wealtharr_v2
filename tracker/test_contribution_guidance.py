"""Financial regressions: new money is not a new scheduled rebalance."""
from datetime import timedelta
from decimal import Decimal as D
from types import SimpleNamespace as NS
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.contribution_guidance import new_money_guidance, uninvested_deposits
from tracker.instrument_mapping import MAPPING_VERSION
from tracker.core.catalogue import onboarding_manifest
from tracker.models import (Account, LedgerEntry, MarketObservation, PriceMark,
                            SignalFill, StrategyInstance, StrategyPreset, StrategySignal)
from tracker.services import ledger_summary, validate_and_record
from tracker.strategies.contributions import allocation
from tracker.strategies.registry import current_review_range, describe_strategy


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

    def test_rules_description_matches_between_review_new_cash_policy(self):
        for key in ("all-weather", "golden-butterfly", "hfea",
                    "permanent-portfolio", "dual-momentum"):
            wording = " ".join(describe_strategy(key).rules).lower()
            self.assertNotIn("new money waits as cash", wording)
            self.assertIn("new deposits", wording)

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
            mapping_version=MAPPING_VERSION,
            resolved_instruments=onboarding_manifest(key, "TRADING212", "ISA"),
            started_on=timezone.localdate() - timedelta(days=12),
            owner_checked_on=timezone.localdate() - timedelta(days=12),
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
            MarketObservation.objects.create(
                symbol=symbol, as_of=self.day, price=price, currency="GBP")

    def funded_strategy(self):
        self.record("DEPOSIT", amount="1000")
        self.record("BUY", symbol="LQQ3.L", quantity="6", price="100")
        self.record("BUY", symbol="ERNS.L", quantity="8", price="50")
        self.record("DEPOSIT", amount="500")
        self.prices()

    def test_overview_and_today_both_surface_cash_waiting_for_investment(self):
        self.funded_strategy()
        self.client.force_login(self.owner)
        today = self.client.get(reverse("v2_instance_page", args=[self.strategy.pk, "today"]))
        overview = self.client.get(reverse("v2_overview"))
        self.assertContains(today, "Invest your new deposit")
        self.assertContains(overview, "Invest new deposit")
        self.assertContains(overview, "500")

    def test_urgent_warning_blocks_today_overview_and_new_cash_notification(self):
        from tracker.cockpit_notifications import send_due_cockpit_notices
        self.funded_strategy()
        previous, _ = current_review_range(self.strategy, timezone.localdate())
        warning = StrategySignal.objects.create(
            strategy=self.strategy, kind="THIRTY_DOWN",
            as_of=previous, signal_key="urgent:cash-priority",
            title="Urgent manual check", message="Review before acting.")
        self.client.force_login(self.owner)
        report = ledger_summary(self.strategy)
        self.assertIsNone(new_money_guidance(self.strategy, report, prices_ready=True))
        self.assertContains(self.client.get(reverse("v2_overview")), "Manual market review")
        self.assertContains(self.client.get(reverse("v2_instance_page", args=[self.strategy.pk, "today"])),
                            "Urgent manual check")
        with patch("tracker.cockpit_notifications._send", return_value=1) as send:
            send_due_cockpit_notices()
        self.assertFalse(any(call.args[2] == "contribution" for call in send.call_args_list))
        warning.acknowledged_at = timezone.now()
        warning.save(update_fields=["acknowledged_at"])
        self.assertIsNotNone(new_money_guidance(self.strategy, report, prices_ready=True))

    def test_overview_suppresses_original_quantities_after_partial_fill(self):
        self.funded_strategy()
        previous, _ = current_review_range(self.strategy, timezone.localdate())
        review = StrategySignal.objects.create(
            strategy=self.strategy, kind="QUARTERLY",
            as_of=previous, signal_key="quarter:partial-overview",
            title="Quarterly proposal", message="Original estimate",
            actions=[{"side": "BUY", "symbol": "ERNS.L",
                      "quantity": "10", "estimated_gbp": "500"}])
        actual = self.record("BUY", symbol="ERNS.L", quantity="1", price="50")
        SignalFill.objects.create(
            strategy=self.strategy, signal=review, ledger_entry=actual,
            action_index=0, idempotency_key="overview-fill-1", recorded_by=self.owner)
        self.client.force_login(self.owner)
        overview = self.client.get(reverse("v2_overview"))
        self.assertContains(overview, "Reconcile partial fills")
        self.assertNotContains(overview, "Buy ≈")
        self.assertNotContains(overview, "Invest new deposit")

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

    def test_today_shows_new_cash_card_after_verified_close(self):
        self.record("DEPOSIT", amount="1000")
        self.record("BUY", symbol="LQQ3.L", quantity="6", price="100")
        self.record("BUY", symbol="ERNS.L", quantity="8", price="50")
        self.record("DEPOSIT", amount="500")
        self.prices()
        self.client.force_login(self.owner)
        response = self.client.get(reverse("v2_instance_page", args=[
            self.strategy.pk, "today"]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invest your new deposit")
        self.assertContains(response, "ERNS.L")
        self.assertContains(response, "500.00")
        self.assertContains(response, "Record what I bought")

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
