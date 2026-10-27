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
                symbol=symbol, as_of=self.day, price=price, currency="GBP",
                source="yahoo", raw_unit_verified=True, raw_quote_unit="GBP", raw_close=price)

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

    def test_mixed_date_overview_is_labelled_indicative(self):
        second_account = Account.objects.create(
            owner=self.owner, name="Second ISA", kind="ISA",
            country="GB", broker="TRADING212")
        second = StrategyInstance.objects.create(
            owner=self.owner, account=second_account, preset=self.strategy.preset,
            name="Second tracked strategy", tracking_only=False)
        first_date = timezone.localdate() - timedelta(days=1)
        second_date = timezone.localdate() - timedelta(days=3)

        def history(instance):
            day = first_date if instance.pk == self.strategy.pk else second_date
            return {"actual_net_funding": D("1000"),
                    "latest_certified": NS(as_of=day, value_gbp=D("1200")),
                    "status": "verified_market", "reason": "Test only",
                    "last_point": None}

        self.client.force_login(self.owner)
        with patch("tracker.v2_history.build_history", side_effect=history), patch(
                "tracker.v2_history._overview_extras", return_value={
                    "ov_chart": {"empty": True, "span": "all", "mode": "gbp"},
                    "ov_ranges": (), "ov_todos": (), "ov_assets": (),
                    "ov_coming": (), "ov_alerts": {}, "ov_today": timezone.localdate(),
                }):
            overview = self.client.get(reverse("v2_overview"))
        self.assertEqual(overview.status_code, 200)
        self.assertFalse(overview.context["overview_same_date"])
        self.assertTrue(overview.context["overview_mixed_dates"])
        self.assertContains(overview, "Indicative sum of different closing dates")
        self.assertEqual(overview.context["overview_total"], D("2400"))

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


class GuidanceEvidenceTests(TestCase):
    """The helper enforces price evidence itself, whatever the caller says."""
    setUp = LiveContributionGuidanceTests.setUp
    record = LiveContributionGuidanceTests.record
    prices = LiveContributionGuidanceTests.prices
    funded_strategy = LiveContributionGuidanceTests.funded_strategy

    def guidance(self):
        return new_money_guidance(self.strategy, ledger_summary(self.strategy), prices_ready=True)

    def test_verified_prices_give_rows_with_the_close_date_and_source(self):
        self.funded_strategy()
        result = self.guidance()
        self.assertIsNotNone(result)
        for row in result["rows"]:
            self.assertEqual((row["price_date"], row["price_source"]), (self.day, "yahoo"))

    def test_unverified_or_non_yahoo_observations_are_refused(self):
        self.funded_strategy()
        for change in ({"raw_unit_verified": False}, {"source": ""}, {"source": "stooq"}):
            MarketObservation.objects.update(**change)
            self.assertIsNone(self.guidance(), change)
            MarketObservation.objects.update(source="yahoo", raw_unit_verified=True)
        self.assertIsNotNone(self.guidance())

    def test_an_observation_in_another_currency_is_refused(self):
        self.funded_strategy()
        MarketObservation.objects.update(currency="USD")
        self.assertIsNone(self.guidance())

    def test_a_held_price_is_refused(self):
        from tracker.models import YahooSync
        self.funded_strategy()
        YahooSync.objects.create(symbol="ERNS.L", last_error="PRICE_HOLD:LARGE_MOVE")
        self.assertIsNone(self.guidance())

    def test_quotes_older_than_a_week_are_refused(self):
        self.funded_strategy()
        PriceMark.objects.update(as_of=self.day - timedelta(days=30))
        self.assertIsNone(self.guidance())


class DollarAccountGuidanceTests(TestCase):
    def test_a_dollar_account_is_guided_in_dollars_from_its_own_closes(self):
        from tracker.strategies.instruments import role_symbols
        owner = get_user_model().objects.create_user("usd-guidance", password="test-only-wealtharr")
        account = Account.objects.create(
            owner=owner, name="Fidelity", kind="BROKERAGE", country="US", broker="FIDELITY", currency="USD")
        key = "all-weather"
        preset, _ = StrategyPreset.objects.get_or_create(slug=key, defaults={"name": "AW", "active": True})
        strategy = StrategyInstance.objects.create(
            owner=owner, account=account, preset=preset, strategy_key=key, name="AW USD",
            strategy_version=describe_strategy(key).version, mapping_version=MAPPING_VERSION,
            resolved_instruments=onboarding_manifest(key, "FIDELITY", "BROKERAGE"),
            started_on=timezone.localdate() - timedelta(days=12),
            owner_checked_on=timezone.localdate() - timedelta(days=12),
            market_data_enabled=True, tracking_only=False)
        day = timezone.localdate() - timedelta(days=2)
        symbols = role_symbols(strategy)
        validate_and_record(strategy, LedgerEntry(
            occurred_on=day, kind="DEPOSIT", amount=D("1000")))
        for symbol in symbols.values():
            validate_and_record(strategy, LedgerEntry(
                occurred_on=day, kind="BUY", symbol=symbol, quantity=D("1"), unit_price=D("10")))
        validate_and_record(strategy, LedgerEntry(occurred_on=day, kind="DEPOSIT", amount=D("200")))
        for symbol in symbols.values():
            PriceMark.objects.create(strategy=strategy, symbol=symbol, price=D("10"),
                                     as_of=day, source="yahoo")
            MarketObservation.objects.create(
                symbol=symbol, as_of=day, price=D("10"), currency="USD", source="yahoo",
                raw_unit_verified=True, raw_quote_unit="USD", raw_close=D("10"))
        result = new_money_guidance(strategy, ledger_summary(strategy), prices_ready=True)
        self.assertIsNotNone(result)
        self.assertEqual(sum(r["amount"] for r in result["rows"]), result["amount"])
        # The same closes labelled as pounds are not this account's evidence.
        MarketObservation.objects.update(currency="GBP")
        self.assertIsNone(new_money_guidance(strategy, ledger_summary(strategy), prices_ready=True))


class StudyNewMoneyFillTests(TestCase):
    def test_new_money_purchases_follow_the_next_close_setting(self):
        from django.core.cache import cache
        from tracker.strategies.registry import module_for_key
        from tracker.test_study import TODAY, seed
        from tracker.test_study_fixed import seed_all_weather
        cache.clear()
        start = TODAY - timedelta(days=800)
        while start.weekday() > 4:
            start -= timedelta(days=1)
        seed_all_weather(start, start, gold=lambda d, i: 50 + (i % 7) * 3.0)
        study = module_for_key("all-weather").study["GBP"]
        data = study.load_data()
        same = study.run(data, "1y", 1000, 200)
        later = study.run(data, "1y", 1000, 200, "next")
        for result in (same, later):
            s = result["series"]["strategy"]
            self.assertGreater(s["trades"], 0)
            self.assertEqual(s["put_in"], same["series"]["strategy"]["put_in"])
        # The same rules, filled a day later, cannot give the identical path.
        self.assertNotEqual(same["series"]["strategy"]["final"], later["series"]["strategy"]["final"])
        self.assertEqual(len(same["series"]["strategy"]["values"]), len(later["series"]["strategy"]["values"]))
