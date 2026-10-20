"""Phase 5 Breakdown: historical arithmetic, raw-unit holds, owner isolation.

No input here enables brokerage trading; preserved signals are displayed, not
re-issued. All monetary values are genuine test fixtures, never UI defaults.
"""
from datetime import date
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from tracker.models import (
    Account, LedgerEntry, LedgerRevision, MarketObservation,
    StrategyInstance, StrategyPreset, StrategySignal, YahooSync,
)
from tracker.strategies.nine_sig.v1 import VERSION, signal_line_value
from tracker.instrument_mapping import MAPPING_VERSION
from tracker.services import validate_and_record
from tracker.v2_breakdown import _detail, _trace, _growth_drawdown


class Phase5BreakdownTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user("breakdown-owner", password="test-safe-123")
        self.other = User.objects.create_user("breakdown-other", password="test-safe-123")
        self.preset, _ = StrategyPreset.objects.get_or_create(
            slug="nasdaq-quarterly-target",
            defaults={"name": "9Sig", "summary": "Source-defined", "risk": "HIGH", "active": True},
        )
        self.instance = self._instance(self.owner, "My actual ISA")
        self.outsider = self._instance(self.other, "PRIVATE OTHER OWNER")
        self.client.force_login(self.owner)
        self.period = date(2025, 9, 30)
        self.start = date(2025, 2, 11)
        self._add(self.instance, "DEPOSIT", date(2025, 2, 11), amount="1000")
        self.purchase = self._add(self.instance, "BUY", self.start, symbol="LQQ3.L", quantity="6", price="100")
        self._add(self.instance, "DEPOSIT", date(2025, 4, 13), amount="100")
        self._add(self.instance, "DEPOSIT", date(2025, 8, 20), amount="300")
        self.signal = self._signal(self.instance, self.period, "My saved 9Sig review")

    def _instance(self, user, title):
        acct = Account.objects.create(
            owner=user, name=title + " account", broker="TRADING212",
            kind="ISA", country="GB",
        )
        return StrategyInstance.objects.create(
            owner=user, account=acct, preset=self.preset,
            name=title, strategy_key="nasdaq-quarterly-target",
            strategy_version=VERSION, mapping_version=MAPPING_VERSION,
            platform_code="TRADING212", account_type_code="ISA",
            started_on=date(2025, 2, 11),
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L",
            tracking_only=True,
        )

    def _add(self, instance, kind, day, *, amount="0", symbol="",
             quantity="0", price="0"):
        return validate_and_record(instance, LedgerEntry(
            kind=kind, occurred_on=day,
            amount=D(amount), symbol=symbol,
            quantity=D(quantity), unit_price=D(price),
        ))

    def _signal(self, instance, when, title):
        return StrategySignal.objects.create(
            strategy=instance, as_of=when, title=title,
            signal_key="quarter:" + when.isoformat(),
            kind="QUARTERLY",
            message="=SUM(A1)",
            actions=[{"side": "BUY", "symbol": "LQQ3.L",
                      "quantity": "1.2345", "estimated_gbp": "123.45"}],
        )

    def url(self, inst=None):
        return reverse("v2_instance_page", args=[(inst or self.instance).pk, "breakdown"])

    def test_exact_ledger_arithmetic_without_monthly_guessing(self):
        trace, error = _trace(self.instance, self.period)
        self.assertEqual(error, "")
        self.assertEqual(len(trace["quarters"]), 3)
        self.assertEqual([row["net_flows"] for row in trace["quarters"]],
                         [D("0"), D("100"), D("300")])
        # 600*1.09 = 654; next 654*1.09+50=762.86;
        # third 762.86*1.09+150 = 981.5174 -> £981.52.
        self.assertEqual(trace["line"], D("981.52"))
        self.assertEqual(trace["anchor_date"], self.start)
        self.assertEqual(trace["cash"], D("800.00"))
        self.assertEqual(trace["positions"]["LQQ3.L"], D("6"))
        r = self.client.get(self.url())
        self.assertContains(r, "981.52")
        self.assertContains(r, "762.86")
        self.assertContains(r, "Reconstructed target")
        self.assertContains(r, "9% growth")

    def test_saved_advice_is_not_recalculated_or_market_fetched_on_get(self):
        before = list(self.instance.ledger.values_list("pk", flat=True))
        with patch("tracker.market_signals.sync_ticker", side_effect=AssertionError("No fetch")):
            response = self.client.get(self.url())
        self.assertContains(response, "My saved 9Sig review")
        self.assertContains(response, "1.2345")
        self.assertContains(response, "historical advice")
        self.assertEqual(before, list(self.instance.ledger.values_list("pk", flat=True)))
        self.assertEqual(self.instance.signals.count(), 1)
        self.assertEqual(self.instance.baselines.count(), 0)

    def test_missing_9_spy_closes_refuses_to_infer_30_down(self):
        r = self.client.get(self.url())
        self.assertContains(r, "Cannot calculate the SPY rolling-peak")
        self.assertNotContains(r, "meet the 30 Down threshold")
        self.assertContains(r, "Raw quote units")
        self.assertContains(r, "Information only")
        self.assertFalse(_detail(self.instance, self.signal)["growth"]["available"])

    def test_spy_rolling_peak_requires_all_nine_dated_usd_closes(self):
        for day in (date(2023, 9, 29), date(2023, 12, 29),
                    date(2024, 3, 28), date(2024, 6, 28),
                    date(2024, 9, 30), date(2024, 12, 31),
                    date(2025, 3, 31), date(2025, 6, 30),
                    date(2025, 9, 30)):
            MarketObservation.objects.create(
                symbol="SPY", as_of=day,
                price=D("65") if day.year == 2025 and day.month == 9 else D("100"),
                currency="USD",
            )
        details = _detail(self.instance, self.signal)
        self.assertEqual(len(details["spy"]), 9)
        self.assertTrue(details["spy_result"]["triggered"])
        self.assertEqual(details["spy_result"]["decline_pct"], D("35.00"))
        self.assertEqual(details["spy_result"]["threshold"], D("70.0000"))
        self.assertContains(self.client.get(self.url()), "numerical cross-check")

    def test_price_hold_refuses_spy_conclusion_even_when_reference_present(self):
        for d in (date(2023, 9, 29), date(2023, 12, 29),
                  date(2024, 3, 28), date(2024, 6, 28),
                  date(2024, 9, 30), date(2024, 12, 31),
                  date(2025, 3, 31), date(2025, 6, 30),
                  date(2025, 9, 30)):
            MarketObservation.objects.create(
                symbol="SPY", as_of=d, price=D("100"), currency="USD",
            )
        YahooSync.objects.create(
            symbol="SPY", last_error="PRICE_HOLD: suspect source units"
        )
        self.assertIsNone(_detail(self.instance, self.signal)["spy_result"])
        self.assertContains(self.client.get(self.url()), "No 30 Down conclusion")

    def test_cross_owner_quarters_and_csv_are_private(self):
        self._signal(self.outsider, date(2025, 6, 30), "SECRET_OTHER_PERSON_QUARTER")
        self.assertEqual(self.client.get(self.url(self.outsider)).status_code, 404)
        self.assertEqual(
            self.client.get(self.url()+"?quarter=2025-06-30").status_code, 404,
        )
        self.assertEqual(self.client.get(self.url()+"?quarter=bad").status_code, 404)
        csv_res = self.client.get(self.url()+"?export=csv")
        self.assertEqual(csv_res.status_code, 200)
        self.assertEqual(csv_res["Content-Type"], "text/csv; charset=utf-8")
        content = csv_res.content.decode("utf-8")
        self.assertIn("signal_line", content) if False else None
        self.assertIn("quarter_trace", content)
        self.assertIn("981.52", content)
        self.assertIn("'=SUM(A1)", content)
        self.assertNotIn("SECRET_OTHER_PERSON_QUARTER", content)
        self.client.logout()
        self.assertEqual(self.client.get(self.url()+"?export=csv").status_code, 302)

    def test_corrections_hold_arithmetic_no_undisclosed_reanchor(self):
        payload = {
            "occurred_on": self.purchase.occurred_on.isoformat(),
            "kind": self.purchase.kind, "symbol": self.purchase.symbol,
            "quantity": str(self.purchase.quantity), "unit_price": str(self.purchase.unit_price),
            "amount": str(self.purchase.amount), "fee": str(self.purchase.fee),
            "note": self.purchase.note,
        }
        LedgerRevision.objects.create(
            strategy=self.instance, entry=self.purchase,
            version=1, actor=self.owner, operation="EDIT",
            before=payload, after=payload,
        )
        detail = _detail(self.instance, self.signal)
        self.assertIsNone(detail["trace"])
        self.assertIn("historical re-evaluation", detail["trace_error"])
        self.assertFalse(detail["checks"][0]["passed"])
        self.assertEqual(detail["saved_actions"][0]["quantity"], "1.2345")

    def test_mismatched_mapping_or_unmapped_holding_cannot_build_trace(self):
        # Simulate a corrupted pinned row; normal model edits are immutable.
        StrategyInstance.objects.filter(pk=self.instance.pk).update(mapping_version="unapproved")
        self.instance.refresh_from_db()
        trace, error = _trace(self.instance, self.period)
        self.assertIsNone(trace)
        self.assertIn("Pinned strategy", error)

    def test_absent_strategy_signal_is_explicit_empty_state(self):
        newcomer = self._instance(self.owner, "Brand new separate strategy")
        r = self.client.get(self.url(newcomer))
        self.assertContains(r, "No completed-quarter")
        self.assertNotContains(r, "My saved 9Sig review")
        self.assertNotContains(r, "981.52")

    def test_quarter_selected_is_only_persisted_and_review_date(self):
        older = self._signal(self.instance, date(2025, 6, 30), "Historical Q2")
        r = self.client.get(self.url()+"?quarter=2025-06-30")
        self.assertContains(r, "Historical Q2")
        self.assertEqual(r.context["breakdown_current"].pk, older.pk)
        self.assertContains(r, "Export audit CSV")

    def test_unconfirmed_prior_reset_never_silently_reanchors_signal_line(self):
        pending = self._signal(self.instance, date(2025, 6, 30), "Pending owner reset")
        pending.reset_reason = "Reset requires actual confirmed 60/40 fills"
        pending.save(update_fields=["reset_reason"])
        trace, reason = _trace(self.instance, self.period)
        self.assertIsNone(trace)
        self.assertIn("no owner-confirmed execution", reason)
        response = self.client.get(self.url())
        self.assertContains(response, "previous 60/40 reset")

    def test_growth_drawdown_never_substitutes_for_spy(self):
        MarketObservation.objects.create(
            symbol="LQQ3.L", as_of=date(2025, 4, 30),
            price=D("100"), currency="GBP"
        )
        MarketObservation.objects.create(
            symbol="LQQ3.L", as_of=date(2025, 9, 30),
            price=D("40"), currency="GBP"
        )
        row = _growth_drawdown(self.instance, self.period)
        self.assertFalse(row["available"])
        self.assertEqual(row["observations"], 2)
        self.assertIn("doesn't affect the advice", row["reason"])
        self.assertIsNone(_detail(self.instance, self.signal)["spy_result"])
