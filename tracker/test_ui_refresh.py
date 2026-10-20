"""UI contracts: navigation, verified financial values and tabbed manual workflows."""
from datetime import date
from decimal import Decimal as D
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from tracker.models import (
    Account, LedgerEntry, StrategyInstance, StrategyPreset, StrategySignal,
    StrategyValueSnapshot,
)
from tracker.presentation import sparkline_path


class SparklineTests(SimpleTestCase):
    def test_no_fabricated_history_with_less_than_two_points(self):
        self.assertEqual(sparkline_path([]), "")
        self.assertEqual(sparkline_path([(date(2026, 1, 1), D("1000"))]), "")

    def test_real_dates_not_equally_spaced(self):
        svg = sparkline_path([
            (date(2026, 1, 1), D("1000")),
            (date(2026, 1, 2), D("1010")),
            (date(2026, 1, 11), D("1200")),
        ])
        self.assertIn("M0.0 32.0", svg)
        self.assertIn("L11.2 30.4", svg)
        self.assertIn("L112.0 0.0", svg)

    def test_flat_line_is_still_a_real_observation(self):
        self.assertEqual(sparkline_path([
            (date(2026, 1, 1), D("400")),
            (date(2026, 1, 2), D("400")),
        ]), "M0.0 16.0 L112.0 16.0")


class PersonalPortalRedesignTests(TestCase):
    """UI regressions now run against v2's real routes and fail-closed values."""

    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="ui-owner", password="secure-unique-ui-password-456")
        self.client.force_login(self.owner)
        self.account = Account.objects.create(
            owner=self.owner, name="ISA", kind="ISA", country="GB",
            broker="TRADING212")
        self.preset = StrategyPreset.objects.get(slug="nasdaq-quarterly-target")
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=self.account, preset=self.preset,
            name="My 9Sig", equity_symbol="LQQ3.L",
            safe_symbol="ERNS.L", market_data_enabled=True)
        self.strategy.ledger.create(
            kind=LedgerEntry.DEPOSIT, amount=D("1000.00"),
            occurred_on=date(2026, 1, 5))

    def url(self, section="strategy"):
        return reverse("v2_instance_page", args=[self.strategy.pk, section])

    def test_overview_shows_real_value_and_accessible_chart(self):
        response = self.client.get(reverse("v2_overview"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["overview_contributed"], D("1000.00"))
        self.assertIsNone(response.context["overview_total"])
        self.assertContains(response, "Recorded net contributions")
        self.assertContains(response, "No combined return chart is certified")
        self.assertContains(response, 'href="#v2-main"')
        self.assertNotContains(response, "Paper portfolio")

    def test_real_strategy_card_sparkline_from_recorded_snapshots_only(self):
        before = self.client.get(self.url("history"))
        self.assertEqual(before.status_code, 200)
        for day, value in ((date(2026, 1, 5), D("1000")),
                           (date(2026, 3, 5), D("1200"))):
            StrategyValueSnapshot.objects.create(
                strategy=self.strategy, as_of=day, total_value=value,
                net_contributed=D("1000"))
        # Persisted snapshots without certified raw source/quote units are
        # historical observations, never instant certified investment returns.
        after = self.client.get(reverse("v2_overview"))
        self.assertIsNone(after.context["overview_total"])
        self.assertIsNone(after.context["overview_gain"])
        self.assertContains(after, "No combined return chart is certified")

    def test_strategy_tabs_preserve_manual_forms_and_instruments(self):
        for section in ("today", "history", "ledger", "strategy", "breakdown"):
            with self.subTest(section=section):
                result = self.client.get(self.url(section))
                self.assertEqual(result.status_code, 200)
                self.assertContains(result, self.strategy.name)
                self.assertNotContains(result, 'id="ledger-form"')
        self.assertContains(self.client.get(self.url("strategy")), "IE00BLRPRL42")
        self.assertContains(self.client.get(self.url("ledger")), "Ledger")

    def test_invalid_tab_does_not_break_page(self):
        # v2 rejects unknown sections rather than serving an unrelated tab.
        response = self.client.get(self.url("unexpected"))
        self.assertEqual(response.status_code, 404)

    def test_signal_review_post_returns_to_signals_tab(self):
        signal = StrategySignal.objects.create(
            strategy=self.strategy, signal_key="quarter:2026-Q3",
            kind="QUARTERLY", as_of=date(2026, 9, 30),
            title="Review 9Sig", message="Review manually", actions=[])
        response = self.client.post(reverse("v2_notice_feedback",
            args=[self.strategy.pk, signal.pk]), {"outcome": "CONFIRMED"})
        self.assertRedirects(response, self.url("today"))
        signal.refresh_from_db()
        self.assertEqual(signal.review_outcome, "CONFIRMED")
        self.assertEqual(self.strategy.ledger.count(), 1)

    def test_invalid_trade_stays_in_ledger_tab(self):
        from tracker.core.catalogue import (
            CATALOGUE_VERSION, onboarding_manifest, released_version)
        from django.utils import timezone
        StrategyInstance.objects.filter(pk=self.strategy.pk).update(
            strategy_key="nasdaq-quarterly-target",
            platform_code="TRADING212", account_type_code="ISA",
            started_on=date(2026, 1, 5),
            owner_checked_on=timezone.localdate(), tracking_only=True,
            strategy_version=released_version("nasdaq-quarterly-target"),
            mapping_version=CATALOGUE_VERSION,
            resolved_instruments=onboarding_manifest(
                "nasdaq-quarterly-target", "TRADING212", "ISA"),
        )
        bad = self.client.post(reverse("v2_ledger_add", args=[self.strategy.pk]), {
            "intent": "preview", "kind": "BUY", "occurred_on": "2026-10-18",
            "symbol": "TQQQ", "quantity": "1", "unit_price": "70",
            "fee": "0", "amount": "",
        })
        self.assertEqual(bad.status_code, 200)
        self.assertContains(bad, "Choose one exact mapped trading line")
        self.assertFalse(self.strategy.ledger.filter(kind=LedgerEntry.BUY).exists())

    def test_false_spike_signal_does_not_offer_reset_confirmation(self):
        from django.utils import timezone
        StrategySignal.objects.create(
            strategy=self.strategy, signal_key="spike:wrong",
            kind="SPIKE_WATCH", as_of=timezone.localdate(),
            title="Reference price was incorrect",
            message="Review a reset to 60% equity / 40% safe",
            actions=[], acknowledged_at=timezone.now(),
            review_outcome="FALSE_POSITIVE")
        page = self.client.get(self.url("today"))
        self.assertNotContains(page, "Choose manual 60/40 reset review")
        self.assertFalse(self.strategy.baselines.exists())

    def test_historical_virtual_records_remain_excluded_from_real_totals(self):
        paper = Account.objects.create(
            owner=self.owner, name="virtual", kind="PAPER")
        demo = StrategyInstance.objects.create(
            owner=self.owner, account=paper, preset=self.preset,
            name="Paper test", equity_symbol="LQQ3.L", safe_symbol="ERNS.L")
        demo.ledger.create(
            kind=LedgerEntry.DEPOSIT, amount=D("123456"),
            occurred_on=date(2026, 1, 5))
        overview = self.client.get(reverse("v2_overview"))
        self.assertEqual(overview.context["overview_contributed"], D("1000.00"))
        self.assertEqual(len(overview.context["overview_rows"]), 1)
        self.assertEqual(
            overview.context["overview_rows"][0]["strategy"].pk,
            self.strategy.pk)
        self.assertNotContains(overview, "123456")
