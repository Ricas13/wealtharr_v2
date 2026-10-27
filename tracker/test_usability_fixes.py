"""Live-strategy ledger, interest, opening positions, cash start, overview and display."""
from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.core.catalogue import CATALOGUE_VERSION, onboarding_manifest, released_version
from tracker.models import (
    Account, LedgerEntry, MarketObservation, StrategyInstance, StrategyPreset, StrategySignal,
)
from tracker.services import ledger_summary, validate_and_record
from tracker.v2_onboarding import OPENING_NOTE, _isa_recorded
from tracker.v2_review_refresh import retire_stale_reviews

KEY = "nasdaq-quarterly-target"


class UsabilityFixesTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="usable-owner", password="secure-unique-usable-password-1")
        self.client.force_login(self.owner)
        self.preset = StrategyPreset.objects.get(slug=KEY)

    def strategy(self, name="9Sig", kind="ISA", *, live=True, start=date(2026, 3, 2)):
        account = Account.objects.create(
            owner=self.owner, name=f"{name} account", kind=kind, country="GB", broker="TRADING212")
        return StrategyInstance.objects.create(
            owner=self.owner, account=account, preset=self.preset, name=name,
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L", strategy_key=KEY,
            platform_code="TRADING212", account_type_code=kind, started_on=start,
            owner_checked_on=start, strategy_version=released_version(KEY),
            mapping_version=CATALOGUE_VERSION,
            resolved_instruments=onboarding_manifest(KEY, "TRADING212", kind),
            onboarding_mode="fresh", tracking_only=not live, market_data_enabled=live)

    def add(self, strategy, data):
        url = reverse("v2_ledger_add", args=[strategy.pk])
        self.assertEqual(self.client.post(url, data).status_code, 200)
        return self.client.post(url, dict(data, intent="confirm"))

    # 1. ledger stays editable after go-live
    def test_live_strategy_ledger_add_and_edit(self):
        s = self.strategy()
        self.assertEqual(self.client.get(reverse("v2_ledger_add", args=[s.pk])).status_code, 200)
        response = self.add(s, {"kind": "DEPOSIT", "occurred_on": "2026-03-02", "amount": "1000", "note": ""})
        self.assertRedirects(response, reverse("v2_instance_page", args=[s.pk, "ledger"]))
        entry = s.ledger.get()
        self.assertEqual(self.client.get(
            reverse("v2_ledger_modify", args=[s.pk, entry.pk, "edit"])).status_code, 200)

    # 2. interest on cash
    def test_interest_is_income_not_a_contribution(self):
        s = self.strategy()
        validate_and_record(s, LedgerEntry(kind="DEPOSIT", amount=D("100"), occurred_on=date(2026, 3, 2)))
        validate_and_record(s, LedgerEntry(kind="INTEREST", amount=D("1.23"), occurred_on=date(2026, 3, 3)))
        report = ledger_summary(s)
        self.assertEqual(report["cash"], D("101.23"))
        self.assertEqual(report["net_contributed"], D("100.00"))


    # 3 + 4. opening position from the wizard, and input kept on error
    BASE = {"strategy": KEY, "platform": "TRADING212", "account_type": "ISA", "ack": "yes", "ack_choice": "yes"}

    def wizard_to_step4(self):
        return reverse("v2_add_strategy")

    def test_wizard_keeps_typed_holdings_after_an_error(self):
        url = self.wizard_to_step4()
        page = self.client.post(url, {**self.BASE, "name": "9Sig", "start": "",
                                      "mode": "hold", "cash": "42.10", "shares_growth": "60",
                                      "cost_growth": "55", "shares_safe": "700", "cost_safe": "5.05"})
        self.assertContains(page, "Enter a valid start date")
        self.assertContains(page, 'value="60"')
        self.assertContains(page, 'value="42.10"')
        self.assertContains(page, 'name="mode" value="hold" checked')

    def test_typed_holdings_become_the_opening_position(self):
        url = self.wizard_to_step4()
        self.client.post(url, {**self.BASE, "name": "9Sig", "start": "2026-03-02",
                               "mode": "hold", "cash": "42.10", "shares_growth": "60",
                               "cost_growth": "55", "shares_safe": "700", "cost_safe": "5"})
        s = StrategyInstance.objects.get(owner=self.owner, name="9Sig")
        report = ledger_summary(s)
        self.assertEqual(report["positions"], {"LQQ3.L": D("60"), "ERNS.L": D("700")})
        self.assertEqual(report["cash"], D("42.10"))
        self.assertEqual(report["net_contributed"], D("6842.10"))
        self.assertFalse(s.ledger.exclude(note=OPENING_NOTE).exists())
        # Not a new ISA subscription, and Today says where the figures came from.
        self.assertEqual(_isa_recorded(self.owner, date(2026, 5, 1))[0], D("0"))
        self.assertContains(self.client.get(reverse("v2_instance_page", args=[s.pk, "today"])),
                            "Based on holdings you entered")


    # 5. first instruction for a cash start
    def test_cash_start_shows_opening_buys_at_target_weights(self):
        s = self.strategy()
        validate_and_record(s, LedgerEntry(kind="DEPOSIT", amount=D("1000"), occurred_on=date(2026, 3, 2)))
        MarketObservation.objects.create(source="yahoo", raw_unit_verified=True, symbol="ERNS.L", as_of=timezone.localdate(), price=D("5"), currency="GBP")
        page = self.client.get(reverse("v2_instance_page", args=[s.pk, "today"]))
        self.assertContains(page, "Opening position from the rules")
        self.assertContains(page, "Buy £600.00 of LQQ3.L")
        self.assertContains(page, "Buy £400.00 of ERNS.L")
        self.assertContains(page, "≈ 80.00 shares")
        self.assertContains(page, "Record what I bought")

    # 1b. corrections retire only the affected unexecuted review
    def test_ledger_change_retires_only_reviews_it_affects(self):
        s = self.strategy()
        review = StrategySignal.objects.create(
            strategy=s, signal_key="quarter:2026-Q3", kind="QUARTERLY", title="Q3",
            message="m", actions=[{"side": "BUY", "symbol": "LQQ3.L", "quantity": "1"}],
            as_of=date(2026, 9, 30))
        self.assertEqual(retire_stale_reviews(s, date(2026, 10, 5)), 0)
        self.assertEqual(retire_stale_reviews(s, date(2026, 9, 1)), 1)
        review.refresh_from_db()
        self.assertEqual(review.review_outcome, "NOT_APPLICABLE")
        self.assertTrue(review.signal_key.startswith("quarter:2026-Q3:superseded:"))

    # 8. overview total ignores a strategy with no value yet
    def test_overview_total_excludes_unvalued_strategies_and_names_them(self):
        a = self.strategy("9Sig")
        validate_and_record(a, LedgerEntry(kind="DEPOSIT", amount=D("1000"), occurred_on=date(2026, 3, 2)))
        b = self.strategy("9Sig GIA", kind="GIA", live=False)
        MarketObservation.objects.all().delete()
        b.ledger.create(kind="BUY", symbol="LQQ3.L", quantity=D("1"), unit_price=D("50"),
                        occurred_on=date(2026, 3, 3))
        page = self.client.get(reverse("v2_overview"))
        self.assertEqual(page.context["overview_total"], D("1000.00"))
        self.assertIn("9Sig GIA", page.context["overview_excluded"])
        self.assertContains(page, "Excludes 9Sig GIA (not valued yet)")

    # 7. display
    def test_display_labels(self):
        s = self.strategy("9Sig")
        validate_and_record(s, LedgerEntry(kind="DEPOSIT", amount=D("12345.6"), occurred_on=date(2026, 3, 2)))
        page = self.client.get(reverse("v2_instance_page", args=[s.pk, "today"]))
        self.assertContains(page, '<span class="w2-nav-label w2-instance-name">9Sig</span>', html=False)
        self.assertContains(page, "ISA · Trading 212")
        self.assertContains(page, "£12,345.60")
