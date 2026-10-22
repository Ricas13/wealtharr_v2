"""Compact dashboard: derived figures, quick-entry forms, review tab, alerts and owner scope."""
from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker import v2_dashboard as dash
from tracker.core.catalogue import CATALOGUE_VERSION, onboarding_manifest, released_version
from tracker.models import (
    Account, LedgerEntry, MarketObservation, StrategyInstance, StrategyPreset,
    StrategySignal, StrategyValueSnapshot,
)
from tracker.services import validate_and_record

KEY = "nasdaq-quarterly-target"


class DerivedFigureTests(TestCase):
    """Pure arithmetic on recorded daily values."""

    def test_growth_index_removes_deposits(self):
        points = [(date(2026, 1, 1), D("1000"), D("1000")),
                  (date(2026, 1, 2), D("1600"), D("1500")),   # +500 deposit, +100 growth
                  (date(2026, 1, 3), D("1600"), D("1500"))]
        growth = dash.growth_index(points)
        self.assertEqual(growth[0][1], D("1"))
        self.assertEqual(growth[1][1], D("1.1"))
        self.assertEqual(growth[2][1], D("1.1"))

    def test_monthly_rows_keep_deposits_out_of_gain(self):
        points = [(date(2026, 3, 31), D("10000"), D("10000")),
                  (date(2026, 4, 30), D("10700"), D("10500"))]
        march, april = reversed(dash.monthly_rows(points))
        self.assertEqual(march["deposits"], D("10000"))
        self.assertEqual(april["deposits"], D("500"))
        self.assertEqual(april["gain"], D("200.00"))
        # Modified Dietz: 200 / (10000 + 250).
        self.assertEqual(april["pct"], D("2.0"))

    def test_drawdown_reports_worst_fall_from_peak(self):
        points = [(date(2026, 1, d), v, D("100")) for d, v in
                  ((1, D("100")), (2, D("120")), (3, D("90")), (4, D("110")))]
        result = dash.drawdown(points)
        self.assertEqual(result["max"], D("-25.0"))
        self.assertEqual(result["current"], D("-8.3"))

    def test_chart_needs_two_points_and_never_invents_any(self):
        one = [(timezone.localdate(), D("100"), D("100"))]
        self.assertTrue(dash.chart(one, span="all", mode="gbp")["empty"])
        two = one + [(timezone.localdate() - timedelta(days=1), D("90"), D("100"))]
        result = dash.chart(sorted(two), span="all", mode="gbp")
        self.assertFalse(result["empty"])
        self.assertEqual([line["label"] for line in result["lines"]], ["Net deposits", "Value"])

    def test_signed_format_filters(self):
        html = Template(
            "{% load w2_format %}{{ a|signed_money }} {{ b|signed_pct }} {{ c|signed_money }} {{ a|tone }}"
        ).render(Context({"a": D("-820.44"), "b": D("2.5"), "c": None}))
        self.assertEqual(html, "−£820.44 +2.5% — w2-neg")


class CompactPagesTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="compact-owner", password="secure-unique-compact-password-1")
        self.other = get_user_model().objects.create_user(
            username="compact-other", password="secure-unique-compact-password-2")
        self.client.force_login(self.owner)
        self.preset = StrategyPreset.objects.get(slug=KEY)
        self.s = self.strategy(self.owner, "Compact 9Sig")
        validate_and_record(self.s, LedgerEntry(
            kind="DEPOSIT", amount=D("1000"), occurred_on=date(2026, 4, 7)))

    def strategy(self, owner, name, *, live=True):
        start = date(2026, 4, 7)
        account = Account.objects.create(
            owner=owner, name=f"{name} account", kind="ISA", country="GB", broker="TRADING212")
        return StrategyInstance.objects.create(
            owner=owner, account=account, preset=self.preset, name=name,
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L", strategy_key=KEY,
            platform_code="TRADING212", account_type_code="ISA", started_on=start,
            owner_checked_on=start, strategy_version=released_version(KEY),
            mapping_version=CATALOGUE_VERSION,
            resolved_instruments=onboarding_manifest(KEY, "TRADING212", "ISA"),
            onboarding_mode="fresh", tracking_only=not live, market_data_enabled=live)

    def url(self, section, strategy=None):
        return reverse("v2_instance_page", args=[(strategy or self.s).pk, section])

    def test_dashboard_has_kpis_quick_forms_and_tabs(self):
        page = self.client.get(self.url("today"))
        self.assertContains(page, "Net deposits")
        self.assertContains(page, "Annualised (XIRR)")
        add = reverse("v2_ledger_add", args=[self.s.pk])
        # Quick forms save through the audited ledger path (with its revision row).
        self.assertContains(page, f'action="{add}"', count=2)
        self.assertContains(page, 'name="intent" value="save"', count=2)
        for tab in ("Dashboard", "Quarterly review", "Ledger", "Performance", "Strategy &amp; alerts"):
            self.assertContains(page, f">{tab}</a>", html=False)

    def test_quick_add_money_saves_in_one_step_with_revision(self):
        from tracker.models import LedgerRevision
        add = reverse("v2_ledger_add", args=[self.s.pk])
        day = timezone.localdate().isoformat()
        response = self.client.post(add, {"intent": "save", "kind": "INTEREST", "return_to": "today",
                                          "occurred_on": day, "amount": "2.50"})
        self.assertRedirects(response, self.url("today"))
        entry = self.s.ledger.get(kind="INTEREST", amount=D("2.50"))
        self.assertTrue(LedgerRevision.objects.filter(entry=entry, operation="ADD").exists())

    def test_one_step_save_still_refuses_negative_cash(self):
        add = reverse("v2_ledger_add", args=[self.s.pk])
        response = self.client.post(add, {"intent": "save", "kind": "WITHDRAW",
                                          "occurred_on": timezone.localdate().isoformat(),
                                          "amount": "5000"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.s.ledger.filter(kind="WITHDRAW").exists())

    def test_wizard_can_go_live_at_creation(self):
        url = reverse("v2_add_strategy")
        self.client.post(url, {"strategy": KEY, "platform": "TRADING212", "account_type": "ISA",
                               "ack": "yes", "mode": "fresh", "name": "Live at once",
                               "start": timezone.localdate().isoformat(), "amount": "500",
                               "deposited": "yes", "go_live": "yes"})
        created = StrategyInstance.objects.get(owner=self.owner, name="Live at once")
        self.assertFalse(created.tracking_only)

    def test_isa_allowance_uses_statutory_limit(self):
        result = dash.isa_allowance([self.s])
        self.assertEqual(result["used"], D("1000"))
        self.assertEqual(result["limit"], D("20000.00"))
        self.assertEqual(result["left"], D("19000.00"))

    def test_review_tab_without_reviews_and_with_one(self):
        self.assertContains(self.client.get(self.url("review")), "No quarterly review yet")
        StrategySignal.objects.create(
            strategy=self.s, signal_key="quarter:test", kind="QUARTERLY",
            title="Test quarterly review", message="Saved advice",
            actions=[{"side": "BUY", "symbol": "LQQ3.L", "quantity": "1", "estimated_gbp": "100"}],
            as_of=date(2026, 9, 30), strategy_version=released_version(KEY))
        page = self.client.get(self.url("review"))
        self.assertContains(page, "Test quarterly review")
        self.assertContains(page, "How this was calculated")

    def test_unverified_monthly_snapshots_are_not_published_as_performance(self):
        for day, value in ((date(2026, 4, 30), D("1010")), (date(2026, 5, 29), D("1030"))):
            StrategyValueSnapshot.objects.create(
                strategy=self.s, as_of=day, total_value=value, net_contributed=D("1000"))
        page = self.client.get(self.url("history"))
        self.assertContains(page, "Monthly")
        self.assertNotContains(page, "+20.00")
        self.assertEqual(dash.certified_points(self.s), [])
        self.assertEqual(len(dash.recorded_points(self.s)), 2)

    def test_index_lines_use_stored_usd_closes_only(self):
        start = timezone.localdate() - timedelta(days=40)
        for i, value in enumerate((D("1000"), D("1100"))):
            StrategyValueSnapshot.objects.create(
                strategy=self.s, as_of=start + timedelta(days=30 * i),
                total_value=value, net_contributed=D("1000"))
        for i, price in enumerate((D("400"), D("440"))):
            MarketObservation.objects.create(
                symbol="QQQ", as_of=start + timedelta(days=30 * i), price=price, currency="USD")
        result = dash.chart(dash.recorded_points(self.s), span="all", mode="pct")
        self.assertIn("Nasdaq-100", [line["label"] for line in result["lines"]])
        self.assertNotIn("S&P 500", [line["label"] for line in result["lines"]])

    def test_strategy_alert_form_saves_and_returns_to_strategy(self):
        page = self.client.get(self.url("strategy"))
        self.assertContains(page, "Alerts for this strategy")
        response = self.client.post(reverse("v2_settings"), {
            "form_kind": "strategy", "strategy_id": self.s.pk, "return_to": "strategy",
            "route_quarterly_email": "on",
        })
        self.assertRedirects(response, self.url("strategy"))
        self.s.refresh_from_db()
        self.assertTrue(self.s.alert_routes["quarterly"]["email"])
        self.assertFalse(self.s.alert_routes["quarterly"]["discord"])

    def test_overview_todo_lists_go_live_and_hides_other_owner(self):
        self.strategy(self.owner, "Waiting strategy", live=False)
        foreign = self.strategy(self.other, "FOREIGN_PRIVATE_STRATEGY")
        page = self.client.get(reverse("v2_overview"))
        self.assertContains(page, "Needs your attention")
        self.assertContains(page, "Waiting strategy")
        self.assertContains(page, "Not live yet")
        self.assertNotContains(page, "FOREIGN_PRIVATE_STRATEGY")
        for section in ("today", "review", "history", "strategy"):
            self.assertEqual(self.client.get(self.url(section, foreign)).status_code, 404)
