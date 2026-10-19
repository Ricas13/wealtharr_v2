"""Section D: independent, exact flow-boundary metric parity and refusal cases.

Artificial numbers here demonstrate the engine's mathematics, NOT a claimed
backtest or the owner's Jason Kelly workbook parity.
"""
from datetime import date
from decimal import Decimal as D
from unittest import TestCase

from tracker.core.metrics import (
    ExternalFlow, FlowBoundary, PortfolioPeriod, VerifiedValuation,
    allocation_drift, evaluate_inception, evaluate_period,
)

START = date(2025, 1, 1)
MID = date(2025, 7, 1)
END = date(2026, 1, 1)


def v(day, value, evidence="quote:verified", verified=True):
    return VerifiedValuation(day, D(str(value)), (evidence,), verified)


def event(identity, day, amount, sequence=1):
    return ExternalFlow(identity, day, D(str(amount)), sequence)


def boundary(flow, before, after, evidence="quote:mid"):
    return FlowBoundary(flow, v(flow.occurred_on, before, evidence),
                        v(flow.occurred_on, after, evidence))


class UniversalMetricTests(TestCase):
    def test_no_flow_plain_growth_is_both_twr_and_gain(self):
        result = evaluate_period(PortfolioPeriod(v(START, "100"),
                                                 v(END, "121")))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.cash_gain_gbp, D("21"))
        self.assertEqual(result.net_external_flows_gbp, D("0"))
        self.assertEqual(result.cumulative_return, D("0.21"))
        self.assertEqual(result.max_drawdown, D("0"))
        self.assertAlmostEqual(result.xirr_annual, D("0.21"), delta=D("0.001"))
        self.assertEqual([a.stage for a in result.audit], ["opening", "closing"])

    def test_deposit_creates_no_artificial_return_and_audits_pre_post(self):
        f = event("deposit:2", MID, "100")
        result = evaluate_period(PortfolioPeriod(
            v(START, "100"), v(END, "231"), (f,),
            (boundary(f, "110", "210"),)))
        # 110 / 100 * 231 / 210 - 1 = 21%, not 131%.
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.cumulative_return, D("0.21"))
        self.assertEqual(result.cash_gain_gbp, D("31"))
        self.assertEqual(result.net_external_flows_gbp, D("100"))
        self.assertEqual(result.max_drawdown, D("0"))
        self.assertEqual([a.stage for a in result.audit],
                         ["opening", "pre_flow:deposit:2",
                          "post_flow:deposit:2", "closing"])
        self.assertEqual(result.audit[1].unit_nav, result.audit[2].unit_nav)
        self.assertEqual(result.audit[2].net_external_flow_gbp, D("100"))
        self.assertGreater(result.audit[2].units_issued, D("0"))
        self.assertGreater(result.audit[2].units_outstanding,
                           result.audit[1].units_outstanding)
        self.assertEqual(result.summary_row()["cumulative_return"], "0.21")
        self.assertEqual(result.audit_rows()[2]["net_external_flow_gbp"], "100")
        self.assertEqual(result.audit_rows()[2]["observation_ids"], "quote:mid")

    def test_withdrawal_is_not_a_negative_return(self):
        f = event("cash-out", MID, "-40")
        result = evaluate_period(PortfolioPeriod(
            v(START, "100"), v(END, "88"), (f,),
            (boundary(f, "120", "80"),)))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.cumulative_return, D("0.32"))
        self.assertEqual(result.cash_gain_gbp, D("28"))
        self.assertEqual(result.net_external_flows_gbp, D("-40"))
        self.assertEqual(result.max_drawdown, D("0"))

    def test_multiple_flows_same_day_use_source_sequence_without_netting(self):
        deposit = event("deposit", MID, "30", 7)
        withdrawal = event("withdrawal", MID, "-20", 9)
        period = PortfolioPeriod(v(START, "100"), v(END, "143"),
            (withdrawal, deposit),
            (boundary(withdrawal, "150", "130"),
             boundary(deposit, "120", "150")))
        result = evaluate_period(period)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.cumulative_return, D("0.32"))
        self.assertEqual(result.cash_gain_gbp, D("33"))
        self.assertEqual([a.stage for a in result.audit],
            ["opening", "pre_flow:deposit", "post_flow:deposit",
             "pre_flow:withdrawal", "post_flow:withdrawal", "closing"])

    def test_without_all_boundaries_only_money_weighted_values_exist(self):
        f = event("deposit", MID, "100")
        result = evaluate_period(PortfolioPeriod(
            v(START, "100"), v(END, "231"), (f,)))
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.reason_code, "MISSING_FLOW_BOUNDARIES")
        self.assertEqual(result.cash_gain_gbp, D("31"))
        self.assertIsNotNone(result.xirr_annual)
        self.assertIsNone(result.cumulative_return)
        self.assertIsNone(result.max_drawdown)
        self.assertIsNone(result.annualized_twr)

    def test_malformed_flow_boundary_is_never_accepted_as_exact(self):
        f = event("deposit", MID, "100")
        wrong_value = evaluate_period(PortfolioPeriod(v(START, "100"), v(END, "231"),
            (f,), (boundary(f, "110", "209"),)))
        self.assertEqual(wrong_value.reason_code, "FLOW_BOUNDARY_NOT_EXACT")
        self.assertIsNone(wrong_value.cumulative_return)
        wrong_evidence = evaluate_period(PortfolioPeriod(v(START, "100"), v(END, "231"),
            (f,), (FlowBoundary(f, v(MID, "110", "quote:a"),
                                   v(MID, "210", "quote:b")),)))
        self.assertEqual(wrong_evidence.reason_code, "FLOW_BOUNDARY_NOT_EXACT")
        self.assertIsNone(wrong_evidence.max_drawdown)

    def test_same_day_daily_mark_around_deposit_is_ambiguous(self):
        f = event("deposit", MID, "100")
        result = evaluate_period(PortfolioPeriod(v(START, "100"), v(END, "231"),
            (f,), (boundary(f, "110", "210"),), (v(MID, "210"),)))
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.reason_code, "AMBIGUOUS_INTERMEDIATE_ORDER")

    def test_extra_missing_duplicate_or_backdated_flows_refused(self):
        a = event("a", MID, "100", 4)
        b = event("b", MID, "20", 4)  # same date/source sequence is ambiguous
        for flows in ((a, b), (a, a), (event("future", date(2026, 2, 1), "1"),),
                      (event("unstarted", START, "2"),)):
            result = evaluate_period(PortfolioPeriod(
                v(START, "100"), v(END, "231"), flows))
            self.assertEqual(result.reason_code, "INVALID_EXTERNAL_FLOW_LEDGER")

    def test_unitised_drawdown_excludes_a_large_deposit(self):
        f = event("topup", MID, "900")
        result = evaluate_period(PortfolioPeriod(v(START, "100"), v(END, "918"),
            (f,), (boundary(f, "120", "1020"),),
            (v(date(2025, 4, 1), "120"),
             v(date(2025, 11, 1), "918"))))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.max_drawdown, D("0.10"))
        self.assertEqual(result.cumulative_return, D("0.08"))
        self.assertEqual(result.cash_gain_gbp, D("-82"))
        self.assertEqual(result.audit[-1].unit_nav, D("1.08"))

    def test_unverified_valuation_currency_cannot_calculate(self):
        invalid = VerifiedValuation(END, D("110"), ("source",), True, "USD")
        for closing in (invalid, v(END, "110", verified=False),
                        VerifiedValuation(END, D("110"), (), True)):
            result = evaluate_period(PortfolioPeriod(v(START, "100"), closing))
            self.assertEqual(result.status, "cannot_compute")
            self.assertIsNone(result.cash_gain_gbp)
            self.assertIsNone(result.xirr_annual)

    def test_full_loss_and_internal_fee_are_not_external_flows(self):
        total_loss = evaluate_period(PortfolioPeriod(v(START, "100"), v(END, "0")))
        self.assertEqual(total_loss.cumulative_return, D("-1"))
        self.assertEqual(total_loss.max_drawdown, D("1"))
        charged = evaluate_period(PortfolioPeriod(v(START, "100"), v(END, "90")))
        self.assertEqual(charged.cash_gain_gbp, D("-10"))
        self.assertEqual(charged.cumulative_return, D("-0.1"))

    def test_different_strategy_roles_and_cash_drift(self):
        drift = allocation_drift(
            {"growth": D("650"), "safe": D("350")},
            {"growth": D("0.60"), "safe": D("0.40")})
        self.assertEqual({d.role: d.drift_percentage_points for d in drift},
                         {"growth": D("5.00"), "safe": D("-5.00")})
        self.assertIsNone(allocation_drift(
            {"growth": D("650"), "safe": D("350"), "cash": D("1")},
            {"growth": D("0.60"), "safe": D("0.40")}))
        self.assertIsNone(allocation_drift(
            {"growth": D("650"), "safe": D("350")},
            {"growth": D("0.61"), "safe": D("0.40")}))


class InceptionMetricTests(TestCase):
    def test_single_funding_has_actual_simple_return(self):
        metric = evaluate_inception((event("dep", START, "1000"),),
                                    v(END, "1100"))
        self.assertEqual(metric.status, "ok")
        self.assertEqual(metric.cash_gain_gbp, D("100"))
        self.assertEqual(metric.simple_cumulative_return, D("0.1"))
        self.assertAlmostEqual(metric.xirr_annual, D("0.1"), delta=D("0.001"))

    def test_irregular_funding_has_gain_and_xirr_but_no_fake_cumulative(self):
        rows = (event("initial", START, "1000"),
                event("june", date(2025, 6, 20), "200"),
                event("oct-withdraw", date(2025, 10, 20), "-100"))
        result = evaluate_inception(rows, v(END, "1300"))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.net_contributed_gbp, D("1100"))
        self.assertEqual(result.cash_gain_gbp, D("200"))
        self.assertIsNotNone(result.xirr_annual)
        self.assertIsNone(result.simple_cumulative_return)
        self.assertEqual(result.audit_rows()[1]["signed_external_flow_gbp"], "200")
        self.assertEqual(result.audit_rows()[2]["signed_external_flow_gbp"], "-100")

    def test_end_without_verified_evidence_and_missing_start(self):
        self.assertEqual(evaluate_inception(
            (event("dep", START, "100"),),
            v(END, "110", verified=False)).status, "cannot_compute")
        self.assertEqual(evaluate_inception(
            (event("wrong", START, "-100"),),
            v(END, "110")).reason_code, "MISSING_INITIAL_FUNDING")
        self.assertEqual(evaluate_inception(
            (event("late", date(2026, 2, 1), "100"),),
            v(END, "110")).status, "cannot_compute")
