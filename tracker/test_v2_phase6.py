"""Phase 6: no fabricated investment returns, exact real ledger cash history."""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch

from tracker.core.metrics import METRICS_VERSION
from tracker.models import (
    Account, InstanceOpeningSnapshot, LedgerEntry, LedgerRevision,
    MarketObservation, StrategyInstance, StrategyPreset, StrategyValueSnapshot,
)
from tracker.services import validate_and_record
from tracker.v2_history import build_history


class V2HistoryTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user("history-owner", password="test-secure-123")
        self.other = User.objects.create_user("history-other", password="test-secure-123")
        self.preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name":"9Sig","summary":"Source-controlled","risk":"HIGH","active":True},
        )
        self.day = timezone.localdate()
        self.start = self.day-timedelta(days=90)
        self.account = self._instance(self.owner, "Main cash ISA")
        self.private = self._instance(self.other, "PRIVATE_OTHER_PERSON_123")
        self.client.force_login(self.owner)

    def _instance(self, owner, name, *, kind="ISA"):
        acct = Account.objects.create(
            owner=owner, name=name+" wrapper", kind=kind,
            broker="TRADING212", country="GB",
        )
        return StrategyInstance.objects.create(
            owner=owner, account=acct, preset=self.preset,
            name=name, equity_symbol="LQQ3.L", safe_symbol="ERNS.L",
            tracking_only=True,
        )

    def _record(self, strategy=None, *, kind="DEPOSIT", amount="1000",
                day=None, symbol="", qty="0", price="0"):
        return validate_and_record(strategy or self.account, LedgerEntry(
            kind=kind, amount=D(amount), occurred_on=day or self.start,
            symbol=symbol, quantity=D(qty), unit_price=D(price),
        ))

    def _snapshot(self, strategy, day, value, funding):
        return StrategyValueSnapshot.objects.create(
            strategy=strategy, as_of=day, total_value=D(value),
            net_contributed=D(funding),
        )

    def _url(self, inst=None):
        return reverse("v2_instance_page", args=[(inst or self.account).pk,"history"])

    def test_genuine_cash_ledger_supports_inception_xirr_zero(self):
        self._record()
        report = build_history(self.account)
        self.assertEqual(report["status"], "verified_cash")
        self.assertEqual(report["latest_certified"].value_gbp, D("1000"))
        self.assertEqual(report["inception"].cash_gain_gbp, D("0"))
        self.assertLess(abs(report["inception"].xirr_annual), D("1e-12"))
        self.assertEqual(report["inception"].simple_cumulative_return, D("0"))
        self.assertEqual(len(report["points"]), 1)
        self.assertEqual(report["points"][0].origin, "Replayed GBP ledger cash")
        self.assertTrue(report["points"][0].certified)
        self.assertContains(self.client.get(self._url()), "Ledger-backed cash endpoint")
        self.assertContains(self.client.get(self._url()), "£1000.00")

    def test_irregular_deposits_and_withdrawals_never_become_return(self):
        self._record(amount="1000")
        self._record(amount="125", day=self.start+timedelta(days=17))
        self._record(kind="WITHDRAW", amount="75",
                     day=self.start+timedelta(days=44))
        result = build_history(self.account)
        self.assertEqual(result["actual_net_funding"], D("1050"))
        self.assertEqual(result["latest_certified"].value_gbp, D("1050"))
        self.assertEqual(result["inception"].cash_gain_gbp, D("0"))
        self.assertLess(abs(result["inception"].xirr_annual), D("1e-12"))
        self.assertIsNone(result["inception"].simple_cumulative_return)
        self.assertEqual([f.amount_gbp for f in result["flows"]],
                         [D("1000"),D("125"),D("-75")])

    def test_stored_investment_snapshot_is_not_verified_or_xirr(self):
        self._record(amount="1000")
        self._record(kind="BUY", amount="0", day=self.start+timedelta(days=1),
                     symbol="LQQ3.L", qty="3", price="100")
        self._snapshot(self.account, self.day-timedelta(days=1), "1350", "1000")
        report = build_history(self.account)
        self.assertEqual(report["status"], "recorded_only")
        self.assertIsNone(report["latest_certified"])
        self.assertIsNone(report["inception"])
        self.assertIsNone(report["period"])
        self.assertFalse(report["points"][0].certified)
        self.assertIn("raw vendor quote-unit", report["reason"])
        page = self.client.get(self._url())
        self.assertContains(page, "£1350.00")
        self.assertContains(page, "Not certified")
        self.assertContains(page, "XIRR")
        self.assertNotContains(page, "1350.00%", html=False)

    def test_matching_market_observation_does_not_invent_missing_raw_unit(self):
        self._record()
        self._record(kind="BUY", amount="0", day=self.start+timedelta(days=1),
                     symbol="LQQ3.L", qty="2", price="100")
        sample_day = self.day-timedelta(days=1)
        self._snapshot(self.account, sample_day, "1100", "1000")
        MarketObservation.objects.create(
            symbol="LQQ3.L", as_of=sample_day,
            price=D("150"), currency="GBP",
        )
        report = build_history(self.account)
        self.assertIsNone(report["inception"])
        self.assertFalse(report["points"][0].certified)
        self.assertIn("raw vendor quote-unit", report["reason"])

    def test_future_and_conflicting_snapshots_cannot_become_verified(self):
        self._record()
        self._snapshot(self.account, self.day-timedelta(days=2), "900", "1000")
        self._snapshot(self.account, self.day+timedelta(days=1), "1000", "1000")
        report = build_history(self.account)
        self.assertEqual(len(report["all_points"]), 2)  # includes actual current ledger cash
        self.assertFalse(report["all_points"][0].certified)
        self.assertTrue(report["all_points"][1].certified)
        self.assertNotIn(self.day+timedelta(days=1),
                         [p.as_of for p in report["all_points"]])

    def test_period_metric_twr_exact_when_no_intermediate_flows(self):
        self._record(amount="1000")
        first_day = self.start+timedelta(days=10)
        self._snapshot(self.account, first_day, "1000", "1000")
        second = self.start+timedelta(days=20)
        self._snapshot(self.account, second, "1000", "1000")
        report = build_history(self.account)
        self.assertEqual(report["period"].status, "ok")
        self.assertEqual(report["period"].cumulative_return, D("0"))
        self.assertEqual(report["period"].max_drawdown, D("0"))
        self.assertLess(abs(report["period"].xirr_annual), D("1e-12"))

    def test_flow_between_verified_points_withholds_twr_missing_boundaries(self):
        self._record(amount="1000")
        self._snapshot(self.account,self.start+timedelta(days=10),"1000","1000")
        self._record(day=self.start+timedelta(days=12),amount="400")
        self._snapshot(self.account,self.start+timedelta(days=30),"1400","1400")
        report = build_history(self.account)
        self.assertIsNotNone(report["period"])
        self.assertEqual(report["period"].status, "partial")
        self.assertIsNone(report["period"].cumulative_return)
        self.assertIsNone(report["period"].max_drawdown)
        self.assertEqual(report["period"].reason_code, "MISSING_FLOW_BOUNDARIES")

    def test_unreconciled_opening_and_ledger_revisions_block_all_metrics(self):
        self._record()
        opening=InstanceOpeningSnapshot.objects.create(
            strategy=self.account,reported_on=self.start,
            cash_gbp=D("1000"),positions={},
        )
        report=build_history(self.account)
        self.assertEqual(report["status"],"blocked")
        self.assertIn("reconciliation",report["reason"])
        opening.delete()
        entry=self.account.ledger.first()
        LedgerRevision.objects.create(
            strategy=self.account,entry=entry,actor=self.owner,
            version=1,operation="EDIT",before={},after={},
        )
        report=build_history(self.account)
        self.assertEqual(report["status"],"blocked")
        self.assertIsNone(report["inception"])

    def test_cross_owner_history_and_overview_never_expose_private_rows(self):
        self._record(self.private, amount="987654")
        self._snapshot(self.private,self.day-timedelta(days=1),"999888","987654")
        self.assertEqual(self.client.get(self._url(self.private)).status_code,404)
        for url in (self._url(),reverse("v2_overview")):
            response=self.client.get(url)
            self.assertEqual(response.status_code,200)
            self.assertNotContains(response,"PRIVATE_OTHER_PERSON_123")
            self.assertNotContains(response,"987654")
            self.assertNotContains(response,"999888")
        self.client.logout()
        self.assertEqual(self.client.get(reverse("v2_overview")).status_code,302)
        self.assertEqual(self.client.get(self._url()).status_code,302)

    def test_overview_all_or_nothing_even_with_one_verified_cash_account(self):
        self._record(amount="1000")
        sibling=self._instance(self.owner,"Second ISA")
        self._record(sibling,amount="500")
        page=self.client.get(reverse("v2_overview"))
        self.assertContains(page,"Combined verified value")
        self.assertEqual(page.context["overview_total"],D("1500"))
        self.assertEqual(page.context["overview_contributed"],D("1500"))
        self.assertContains(page,"£1500.00")
        self._record(sibling,amount="0",kind="BUY",
                     day=self.start+timedelta(days=1),symbol="LQQ3.L",
                     qty="2",price="100")
        later=self.client.get(reverse("v2_overview"))
        self.assertIsNone(later.context["overview_total"])
        self.assertIsNone(later.context["overview_gain"])
        self.assertContains(later,"Withheld: some accounts")
        self.assertContains(later,"£1500.00")  # real net contributions persist

    def test_range_filters_recorded_points_but_not_inception_funding(self):
        self._record()
        self._snapshot(self.account,self.day-timedelta(days=10),"1000","1000")
        page=self.client.get(self._url()+"?range=ytd")
        self.assertContains(page,"Source-labelled observation log")
        self.assertEqual(page.context["history"]["range"],"ytd")
        self.assertIn("2026",page.content.decode())
        bad=self.client.get(self._url()+"?range=fake")
        self.assertEqual(bad.context["history"]["range"],"all")

    def test_gets_do_not_sync_market_or_mutate_entries(self):
        self._record()
        before=self.account.ledger.count()
        with patch("tracker.market_signals.sync_ticker",side_effect=AssertionError("unexpected fetch")):
            self.client.get(self._url())
            self.client.get(reverse("v2_overview"))
        self.assertEqual(self.account.ledger.count(),before)
        self.assertFalse(StrategyValueSnapshot.objects.exists())
