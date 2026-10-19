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
        self.strategy.ledger.create(kind=LedgerEntry.DEPOSIT,
                                    amount=D("1000.00"), occurred_on=date(2026, 1, 5))

    def url(self, tab=""):
        base = reverse("strategy_detail", args=[self.strategy.pk])
        return base + ("?tab=" + tab if tab else "")

    def test_overview_is_real_first_and_paper_separate(self):
        result = self.client.get(reverse("dashboard"))
        self.assertEqual(result.status_code, 200)
        html = result.content.decode()
        self.assertLess(html.index("Real portfolio value"), html.index("Paper portfolio"))
        self.assertContains(result, "£1,000.00")
        self.assertContains(result, 'id="wealth-chart"')
        self.assertContains(result, 'id="wealth-chart-points"')
        self.assertContains(result, 'href="#main"')
        self.assertContains(result, "View chart values as a table")

    def test_real_strategy_card_sparkline_from_recorded_snapshots_only(self):
        before = self.client.get(reverse("dashboard"))
        self.assertContains(before, "Trend appears after two dated valuations.")
        for day, value in [(date(2026, 1, 5), D("1000")),
                           (date(2026, 3, 5), D("1200"))]:
            StrategyValueSnapshot.objects.create(
                strategy=self.strategy, as_of=day, total_value=value,
                net_contributed=D("1000"))
        after = self.client.get(reverse("dashboard"))
        self.assertContains(after, 'class="ws-sparkline"')
        self.assertNotContains(after, "Trend appears after two dated valuations.")

    def test_strategy_tabs_preserve_manual_forms_and_instruments(self):
        overview = self.client.get(self.url())
        self.assertEqual(overview.status_code, 200)
        self.assertContains(overview, "What to hold for this account")
        self.assertContains(overview, "IE00BLRPRL42")
        self.assertContains(overview, 'aria-current="page"')
        self.assertNotContains(overview, 'id="ledger-form"')
        signals = self.client.get(self.url("signals"))
        self.assertContains(signals, "Signal history")
        self.assertNotContains(signals, 'id="ledger-form"')
        ledger = self.client.get(self.url("ledger"))
        self.assertContains(ledger, 'id="ledger-form"')
        self.assertContains(ledger, '<option value="LQQ3.L"', html=False)
        setup = self.client.get(self.url("setup"))
        self.assertContains(setup, "Yahoo price monitoring")
        self.assertContains(setup, "IE00BLRPRL42")
        self.assertNotContains(setup, 'id="ledger-form"')

    def test_invalid_tab_does_not_break_page(self):
        response = self.client.get(self.url("unexpected"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_tab"], "overview")

    def test_signal_review_post_returns_to_signals_tab(self):
        signal = StrategySignal.objects.create(
            strategy=self.strategy, signal_key="quarter:2026-Q3",
            kind="QUARTERLY", as_of=date(2026, 9, 30),
            title="Review 9Sig", message="Review manually", actions=[])
        response = self.client.post(
            reverse("review_signal", args=[self.strategy.pk, signal.pk]) +
            "?tab=signals", {"outcome": "CONFIRMED"})
        self.assertRedirects(response, self.url("signals"))
        signal.refresh_from_db()
        self.assertEqual(signal.review_outcome, "CONFIRMED")

    def test_invalid_trade_stays_in_ledger_tab(self):
        bad = self.client.post(
            reverse("record_entry", args=[self.strategy.pk]) +
            "?tab=ledger", {
                "kind": "BUY", "occurred_on": "2026-10-18",
                "symbol": "TQQQ", "quantity": "1", "unit_price": "70",
                "fee": "0", "amount": "",
            })
        self.assertEqual(bad.status_code, 200)
        self.assertEqual(bad.context["active_tab"], "ledger")
        self.assertContains(bad, "Select one of the two 9Sig instruments")
        self.assertFalse(self.strategy.ledger.filter(kind=LedgerEntry.BUY).exists())

    def test_false_spike_signal_does_not_offer_reset_confirmation(self):
        from django.utils import timezone
        StrategySignal.objects.create(
            strategy=self.strategy, signal_key="spike:wrong",
            kind="SPIKE_WATCH", as_of=date(2026, 9, 30),
            title="Reference price was incorrect",
            message="Review a reset to 60% equity / 40% safe",
            actions=[], acknowledged_at=timezone.now(),
            review_outcome="FALSE_POSITIVE")
        page = self.client.get(self.url("signals"))
        self.assertContains(page, "Reference price was incorrect")
        self.assertNotContains(
            page, "Confirm executed reset and start next 9Sig cycle")

    def test_real_and_paper_money_never_mixed(self):
        paper = Account.objects.create(
            owner=self.owner, name="virtual", kind="PAPER")
        demo = StrategyInstance.objects.create(
            owner=self.owner, account=paper, preset=self.preset,
            name="Paper test", equity_symbol="LQQ3.L", safe_symbol="ERNS.L")
        demo.ledger.create(kind=LedgerEntry.DEPOSIT,
                           amount=D("123456"), occurred_on=date(2026, 1, 5))
        overview = self.client.get(reverse("dashboard"))
        self.assertEqual(overview.context["net_contributed"], D("1000.00"))
        self.assertEqual(len(overview.context["items"]), 1)
        self.assertEqual(overview.context["items"][0]["strategy"].pk, self.strategy.pk)
