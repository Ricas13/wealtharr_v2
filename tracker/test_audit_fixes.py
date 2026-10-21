"""Monthly plan, late reviews, confirmed start date and rebuilt value history."""
from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.core.catalogue import CATALOGUE_VERSION, onboarding_manifest, released_version
from tracker.models import (
    Account, LedgerEntry, MarketObservation, RebalanceExecution, SignalFill,
    StrategyGoLive, StrategyInstance, StrategyPreset, StrategySignal, StrategyValueSnapshot,
)
from tracker.review_autofinish import auto_finish_reviews
from tracker.test_g_single_fill import ReviewFixture
from tracker.v2_review_refresh import rebuild_value_history

KEY = "nasdaq-quarterly-target"


class MonthlyPlanTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("plan-owner", password="a-long-test-password-1")
        self.client.force_login(self.owner)

    def test_setup_saves_monthly_plan_and_strategy_page_edits_it(self):
        self.client.post(reverse("v2_add_strategy"), {
            "strategy": KEY, "platform": "TRADING212", "account_type": "ISA", "ack": "yes",
            "mode": "fresh", "name": "Planned", "start": timezone.localdate().isoformat(),
            "amount": "1000", "deposited": "yes", "monthly": "500"})
        strategy = StrategyInstance.objects.get(owner=self.owner)
        self.assertEqual(strategy.monthly_target, D("500"))
        url = reverse("v2_strategy_plan", args=[strategy.pk])
        self.client.post(url, {"monthly_target": "250"})
        strategy.refresh_from_db()
        self.assertEqual(strategy.monthly_target, D("250"))
        self.client.post(url, {"monthly_target": "-1"})
        strategy.refresh_from_db()
        self.assertEqual(strategy.monthly_target, D("250"))
        # Another owner can't change it.
        other = get_user_model().objects.create_user("plan-other", password="a-long-test-password-1")
        self.client.force_login(other)
        self.assertEqual(self.client.post(url, {"monthly_target": "1"}).status_code, 404)
        page_owner = self.client
        page_owner.force_login(self.owner)
        page = page_owner.get(reverse("v2_instance_page", args=[strategy.pk, "strategy"]))
        self.assertContains(page, 'name="monthly_target"')


class LateReviewTests(ReviewFixture):
    """A review whose fills were all recorded, but not finished before the
    next quarter started, can still be finished against those fills."""

    def make_late(self):
        StrategySignal.objects.filter(pk=self.signal.pk).update(reset_reason="")
        self.client.post(self.url, self.fill(0, "1", "100"))
        self.client.post(self.url, self.fill(1, "2", "50"))
        older = self.quarter - timedelta(days=92)
        fills = SignalFill.objects.filter(signal=self.signal).values_list("ledger_entry_id", flat=True)
        self.strategy.ledger.exclude(pk__in=fills).update(occurred_on=older - timedelta(days=10))
        StrategySignal.objects.filter(pk=self.signal.pk).update(as_of=older)
        self.signal.refresh_from_db()

    def test_late_complete_review_can_be_finished_by_button(self):
        self.make_late()
        page = self.client.get(self.url)
        self.assertTrue(page.context["review_late"])
        self.assertTrue(page.context["review_can_finalize"])
        self.assertContains(page, "can still be finished")
        response = self.client.post(self.url, {"intent": "finalize"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(RebalanceExecution.objects.filter(signal=self.signal).exists())

    def test_late_review_is_finished_automatically(self):
        self.make_late()
        self.assertEqual(auto_finish_reviews(), 1)

    def test_no_new_fills_and_no_finish_once_a_later_review_was_acted_on(self):
        self.make_late()
        self.assertEqual(self.client.post(self.url, self.fill(0, "1", "100")).status_code, 302)
        self.assertEqual(SignalFill.objects.filter(signal=self.signal).count(), 2)
        later = StrategySignal.objects.create(
            strategy=self.strategy, kind="QUARTERLY", as_of=self.quarter,
            signal_key="quarter:later:%s" % self.quarter, title="Later", message="",
            actions=[{"side": "BUY", "symbol": "ERNS.L", "quantity": "1", "estimated_gbp": "50"}])
        RebalanceExecution.objects.create(signal=later, strategy=self.strategy,
                                          executed_on=self.trade_day)
        self.assertFalse(self.client.get(self.url).context["review_can_finalize"])
        self.assertEqual(auto_finish_reviews(), 0)
        self.assertFalse(RebalanceExecution.objects.filter(signal=self.signal).exists())


class StartDateTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("start-owner", password="a-long-test-password-1")
        self.client.force_login(self.owner)
        account = Account.objects.create(owner=self.owner, name="ISA", kind="ISA",
                                         country="GB", broker="TRADING212")
        self.strategy = StrategyInstance.objects.create(
            owner=self.owner, account=account, preset=StrategyPreset.objects.get(slug=KEY),
            name="9Sig", equity_symbol="LQQ3.L", safe_symbol="ERNS.L", strategy_key=KEY,
            platform_code="TRADING212", account_type_code="ISA", onboarding_mode="legacy_link",
            strategy_version=released_version(KEY), mapping_version=CATALOGUE_VERSION,
            resolved_instruments=onboarding_manifest(KEY, "TRADING212", "ISA"),
            tracking_only=False, market_data_enabled=True)
        self.strategy.ledger.create(kind="DEPOSIT", amount=D("1000"), occurred_on=date(2026, 9, 1))
        StrategyGoLive.objects.create(strategy=self.strategy, confirmed_by=self.owner,
                                      strategy_version=self.strategy.strategy_version,
                                      mapping_version=CATALOGUE_VERSION, attestation="old")
        self.url = reverse("v2_go_live", args=[self.strategy.pk])

    def test_owner_confirms_the_start_date(self):
        page = self.client.get(reverse("v2_instance_page", args=[self.strategy.pk, "strategy"]))
        self.assertContains(page, 'name="started_on"')
        self.assertContains(page, 'value="2026-09-01"')
        self.client.post(self.url, {"confirm": "yes", "started_on": "2026-10-01"})
        self.strategy.refresh_from_db()
        self.assertIsNone(self.strategy.started_on)  # after the first entry: refused
        self.client.post(self.url, {"confirm": "yes", "started_on": "2026-06-15"})
        self.strategy.refresh_from_db()
        self.assertEqual(self.strategy.started_on, date(2026, 6, 15))


class RebuildValueHistoryTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user("hist-owner", password="a-long-test-password-1")
        account = Account.objects.create(owner=owner, name="ISA", kind="ISA",
                                         country="GB", broker="TRADING212")
        self.strategy = StrategyInstance.objects.create(
            owner=owner, account=account, preset=StrategyPreset.objects.get(slug=KEY),
            name="9Sig", equity_symbol="LQQ3.L", safe_symbol="ERNS.L")
        self.d1, self.d2 = date(2026, 9, 1), date(2026, 9, 10)
        self.strategy.ledger.create(kind="DEPOSIT", amount=D("1000"), occurred_on=self.d1)
        self.buy = self.strategy.ledger.create(kind="BUY", symbol="LQQ3.L", quantity=D("5"),
                                               unit_price=D("100"), occurred_on=self.d1)
        MarketObservation.objects.create(symbol="LQQ3.L", as_of=self.d2, price=D("110"), currency="GBP")
        StrategyValueSnapshot.objects.create(strategy=self.strategy, as_of=self.d2,
                                             total_value=D("1050"), net_contributed=D("1000"))

    def test_backdated_deposit_rebuilds_later_values(self):
        self.strategy.ledger.create(kind="DEPOSIT", amount=D("200"), occurred_on=date(2026, 9, 5))
        rebuild_value_history(self.strategy, date(2026, 9, 5))
        row = StrategyValueSnapshot.objects.get(strategy=self.strategy, as_of=self.d2)
        self.assertEqual((row.total_value, row.net_contributed), (D("1250.00"), D("1200.00")))

    def test_day_without_a_verified_close_is_dropped(self):
        MarketObservation.objects.all().delete()
        rebuild_value_history(self.strategy, self.d1)
        self.assertFalse(StrategyValueSnapshot.objects.filter(strategy=self.strategy).exists())

    def test_values_before_the_change_are_untouched(self):
        self.assertEqual(rebuild_value_history(self.strategy, self.d2 + timedelta(days=1)), 0)
        self.assertEqual(StrategyValueSnapshot.objects.get(as_of=self.d2).total_value, D("1050"))


class ReviewTabFormsTests(ReviewFixture):
    def test_review_tab_forms_post_to_the_review_handler(self):
        page = self.client.get(reverse("v2_instance_page", args=[self.strategy.pk, "review"]))
        self.assertContains(page, 'action="%s"' % self.url)
        self.assertNotContains(page, '<form method="post">')
        # Recording from the tab works (previously a 405).
        self.assertEqual(self.client.post(self.url, self.fill(0, "0.5", "100")).status_code, 302)
