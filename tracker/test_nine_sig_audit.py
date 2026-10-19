"""Extra Section C-only release/audit and deterministic warning tests."""
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from unittest import TestCase

from tracker.strategies.nine_sig.v1 import (
    VERSION, CashFlow, evaluate, first_100_up_crossing,
    signal_line_trace, signal_line_value,
)
from tracker.core.prices import QuoteUnavailable, reference_with_fallback
from tracker.test_nine_sig_v1 import sample, quote


class ReleasedAuditTests(TestCase):
    def test_ledger_cashflows_have_trace_without_monthly_assumptions(self):
        contributions = (
            (date(2026, 2, 3), D("100")),
            (date(2026, 3, 29), D("300")),
            (date(2026, 5, 7), D("100")))
        trace = signal_line_trace(
            start_date=date(2026, 1, 1), as_of=date(2026, 6, 30),
            starting_equity_cost=D("1000"), contributions=contributions)
        self.assertEqual(len(trace), 2)
        self.assertEqual(trace[0][2], D("400"))
        self.assertEqual(trace[0][3], D("1290"))
        self.assertEqual(trace[1][3], D("1456.10"))
        self.assertEqual(signal_line_value(
            start_date=date(2026, 1, 1), as_of=date(2026, 6, 30),
            starting_equity_cost=D("1000"), contributions=contributions),
            D("1456.10"))

    def test_audit_is_csv_safe_and_typed_proposals_are_versioned(self):
        history, bundle = sample()
        result = evaluate(history, bundle)
        self.assertEqual(result.strategy_version, VERSION)
        self.assertEqual(result.status, "action_needed")
        self.assertTrue(result.trades)
        self.assertEqual(result.trades[-1].role, "growth")
        fields = result.audit_row()
        self.assertEqual(fields["strategy_version"], VERSION)
        self.assertEqual(fields["growth_raw_unit"], "GBX")
        self.assertIn("quarter_recurrence", fields)
        self.assertIn("buy_budget", fields)
        self.assertTrue(all(isinstance(x, str) for x in fields.values()))

    def test_unreconciled_pence_and_old_version_cannot_propose(self):
        history, prices = sample()
        altered = replace(prices.equity, raw_close=D("100"))
        result = evaluate(history, replace(prices, equity=altered))
        self.assertEqual(result.status, "cannot_compute")
        self.assertEqual(result.trades, ())
        version = evaluate(replace(history, strategy_version="nine_sig.v9"), prices)
        self.assertEqual(version.reason_code, "UNSUPPORTED_VERSION")
        self.assertFalse(version.trades)

    def test_pure_first_intraperiod_crossing_does_not_look_ahead(self):
        actual = first_100_up_crossing(
            origin_on=date(2026, 3, 31), origin_usd=D("50"),
            daily_usd=((date(2026,4,1),D("98")),
                       (date(2026,4,2),D("100")),
                       (date(2026,4,3),D("120"))),
            until=date(2026,4,2))
        self.assertEqual(actual, (date(2026,4,2),D("100")))
        self.assertIsNone(first_100_up_crossing(
            origin_on=date(2026,3,31), origin_usd=D("50"),
            daily_usd=((date(2026,4,3),D("120")),),
            until=date(2026,4,2)))


class SourceSelectionTests(TestCase):
    def test_fallback_is_explicit_and_does_not_disguise_stooq_as_yahoo(self):
        day = date(2026,3,30)
        anchor = quote("SPY",day,"50","USD")
        calls=[]
        def fake(symbol, since, through, *, yahoo_anchor):
            calls.append((symbol,yahoo_anchor.observation_id))
            return (replace(anchor,source="stooq", observation_id="stooq:SPY:2026-03-30"),)
        chosen = reference_with_fallback(
            "SPY",day,date(2026,3,31),yahoo_quotes=(),
            yahoo_anchor=anchor,fallback_fetch=fake)
        self.assertEqual(chosen[0].source,"stooq")
        self.assertEqual(len(calls),1)
        primary=reference_with_fallback(
            "SPY",day,date(2026,3,31),yahoo_quotes=(anchor,))
        self.assertEqual(primary[0].source,"yahoo")
        with self.assertRaises(QuoteUnavailable):
            reference_with_fallback(
                "ERNS.L",day,date(2026,3,31),yahoo_quotes=())
