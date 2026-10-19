"""Section D refusal/precision regressions for universal metrics.

These numbers are synthetic *unit inputs*, not investor returns or a backtest.
"""
from datetime import date, datetime
from decimal import Decimal as D
from unittest import TestCase

from tracker.core.metrics import (
    ExternalFlow, FlowBoundary, PortfolioPeriod, VerifiedValuation,
    allocation_drift, evaluate_inception, evaluate_period,
)
from tracker.performance import xirr


START = date(2025, 1, 1)
END = date(2026, 1, 1)


def approved(day, amount, marker="accepted:source"):
    return VerifiedValuation(day, D(str(amount)), (marker,), True)


class MetricIntegrityTests(TestCase):
    def test_excel_xirr_act_365_exact_one_year(self):
        # Excel XIRR exponent is actual day difference / 365.
        annual = xirr(((START, D("-100.00")),), END, D("110.00"))
        self.assertIsNotNone(annual)
        self.assertAlmostEqual(annual, D("0.10"), delta=D("0.0000000001"))

    def test_missing_or_malformed_valuation_flow_objects_fail_closed(self):
        self.assertEqual(evaluate_period(None).status, "cannot_compute")
        cases = (
            PortfolioPeriod(approved(START, 100), approved(END, 110),
                            flows=(None,)),
            PortfolioPeriod(approved(START, 100), approved(END, 110),
                            flows=None),
            PortfolioPeriod(approved(START, 100), approved(END, 110),
                            flows=(ExternalFlow("bad-datetime",
                                    datetime(2025, 7, 1), D("10")),)),
        )
        for row in cases:
            with self.subTest(row=row):
                result = evaluate_period(row)
                self.assertEqual(result.reason_code, "INVALID_EXTERNAL_FLOW_LEDGER")
                self.assertIsNone(result.cumulative_return)
        self.assertEqual(
            evaluate_inception((None,), approved(END, "100")).reason_code,
            "INVALID_EXTERNAL_FLOW_LEDGER",
        )
        bad_date = VerifiedValuation(datetime(2026, 1, 1), D("110"),
                                     ("accepted:source",), True)
        self.assertEqual(evaluate_period(PortfolioPeriod(
            approved(START, "100"), bad_date)).status, "cannot_compute")

    def test_missing_or_corrupt_return_boundaries_not_exact(self):
        deposit = ExternalFlow("deposit", date(2025, 7, 1), D("10"))
        bad_boundary = PortfolioPeriod(
            approved(START, 100), approved(END, 121), (deposit,), (None,))
        self.assertEqual(evaluate_period(bad_boundary).reason_code,
                         "FLOW_BOUNDARY_MISMATCH")
        wrong_observations = PortfolioPeriod(
            approved(START, 100), approved(END, 121), (),
            (), None)
        result = evaluate_period(wrong_observations)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.reason_code, "INVALID_RETURN_OBSERVATIONS")
        self.assertIsNone(result.max_drawdown)

    def test_closing_day_deposit_requires_price_evidence_of_order(self):
        deposit = ExternalFlow("late-topup", END, D("100"))
        prior = approved(END, 110, "quote:close")
        after = approved(END, 210, "quote:close")
        boundary = FlowBoundary(deposit, prior, after)
        ambiguous = evaluate_period(PortfolioPeriod(
            approved(START, 100), approved(END, 210, "quote:other"), (deposit,),
            (boundary,)))
        self.assertEqual(ambiguous.reason_code,
                         "AMBIGUOUS_CLOSING_DAY_FLOW_ORDER")
        self.assertIsNone(ambiguous.cumulative_return)
        self.assertIsNotNone(ambiguous.cash_gain_gbp)
        verified = evaluate_period(PortfolioPeriod(
            approved(START, 100), approved(END, 210, "quote:close"), (deposit,),
            (boundary,)))
        self.assertEqual(verified.status, "ok")
        self.assertEqual(verified.cumulative_return, D("0.1"))

    def test_total_loss_reports_negative_one_return_and_drawdown(self):
        metrics = evaluate_period(PortfolioPeriod(
            approved(START, 100), approved(END, 0)))
        self.assertEqual(metrics.status, "ok")
        self.assertEqual(metrics.cumulative_return, D("-1"))
        self.assertEqual(metrics.annualized_twr, D("-1"))
        self.assertEqual(metrics.max_drawdown, D("1"))
        self.assertIsNone(metrics.xirr_annual)

    def test_irregular_flows_zero_xirr_when_closing_equals_net_funding(self):
        flows = (
            ExternalFlow("initial", START, D("1000"), 0),
            ExternalFlow("june", date(2025, 6, 5), D("500"), 0),
            ExternalFlow("autumn", date(2025, 10, 22), D("-100"), 0),
        )
        result = evaluate_inception(flows, approved(END, "1400"))
        self.assertEqual(result.net_contributed_gbp, D("1400"))
        self.assertEqual(result.cash_gain_gbp, D("0"))
        self.assertEqual(result.gain_vs_net_contributions_pct, D("0"))
        self.assertIsNone(result.simple_cumulative_return)
        self.assertIsNotNone(result.xirr_annual)
        self.assertAlmostEqual(result.xirr_annual, D("0"),
                               delta=D("0.0000000001"))

    def test_unknown_roles_cannot_disappear_from_allocation_calculation(self):
        self.assertIsNone(allocation_drift(
            {"growth": D("500"), "safe": D("400"), "unmapped": D("100")},
            {"growth": D("0.6"), "safe": D("0.4")}))
        self.assertIsNone(allocation_drift(
            {"growth": D("0"), "safe": D("0")},
            {"growth": D("0.6"), "safe": D("0.4")}))

    def test_gain_percentage_is_descriptive_not_twr(self):
        flows = (
            ExternalFlow("initial", START, D("1000"), 0),
            ExternalFlow("later", date(2025, 6, 20), D("200"), 0),
            ExternalFlow("withdraw", date(2025, 10, 20), D("-100"), 0),
        )
        result = evaluate_inception(flows, approved(END, D("1300")))
        self.assertEqual(result.cash_gain_gbp, D("200"))
        self.assertEqual(result.net_contributed_gbp, D("1100"))
        self.assertAlmostEqual(result.gain_vs_net_contributions_pct,
                               D("18.1818181818"), delta=D("0.00000001"))
        self.assertIsNone(result.simple_cumulative_return)

    def test_gain_percentage_unavailable_when_net_funding_zero_or_negative(self):
        flows = (
            ExternalFlow("first", START, D("100"), 0),
            ExternalFlow("cash-out", date(2025, 6, 1), D("-100"), 0),
        )
        result = evaluate_inception(flows, approved(END, "10"))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.net_contributed_gbp, D("0"))
        self.assertIsNone(result.gain_vs_net_contributions_pct)
        self.assertIsNone(result.simple_cumulative_return)
