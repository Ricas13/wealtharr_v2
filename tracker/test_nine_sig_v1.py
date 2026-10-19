"""Section C: pure 9Sig v1 contract and independent-price adapter tests.

Owner workbook fixtures are intentionally EMPTY, never synthetic 'parity' data.
"""
import csv
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from io import BytesIO
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

from tracker.strategies.nine_sig.v1 import (
    PARAMS, VERSION, ApprovedPriceBundle, CashFlow, NormalizedQuote,
    PortfolioHistory, evaluate,
)
from tracker.core.prices import QuoteUnavailable, fetch_stooq_reference, reference_with_fallback

FIXTURE_DIR = Path(__file__).parent / "strategies" / "nine_sig" / "fixtures"
PARITY_FIELDS = (
    "case_id", "quarter_end", "start_date", "starting_equity_gbp",
    "equity_qty", "safe_qty", "cash_gbp", "lqq3_close_gbp",
    "erns_close_gbp", "prior_30down", "down_age_quarters",
    "prior_skipped_sells", "expected_signal_line_gbp",
    "expected_equity_value_gbp", "expected_buy_equity_qty",
    "expected_sell_equity_qty", "expected_buy_safe_qty",
    "expected_sell_safe_qty", "expected_30down",
    "expected_spike_warning", "expected_reset_review",
    "expected_cash_after_estimated_orders_gbp",
)


def quote(symbol, day, price, currency, raw=None, source="yahoo"):
    return NormalizedQuote(symbol, day, D(str(price)), currency, currency,
                           source, raw or currency, True,
                           raw_close=D(str(price)) * (D("100") if raw == "GBX" else D("1")),
                           observation_id="fixture:%s:%s" % (symbol, day))


def sample():
    as_of = date(2026, 3, 31)
    history = PortfolioHistory(
        start_date=date(2025, 10, 1), as_of=as_of,
        equity_symbol="LQQ3.L", safe_symbol="ERNS.L",
        equity_qty=D("10"), safe_qty=D("10"), cash=D("100"),
        initial_equity_value=D("1000"),
        contributions=(
            CashFlow(date(2025, 10, 7), D("120")),
            CashFlow(date(2025, 12, 19), D("80")),
            CashFlow(date(2026, 2, 1), D("50")),
        ))
    prior = [date(y, m, d) for y, m, d in [
        (2024, 3, 31), (2024, 6, 30), (2024, 9, 30),
        (2024, 12, 31), (2025, 3, 31), (2025, 6, 30),
        (2025, 9, 30), (2025, 12, 31)]]
    prices = ApprovedPriceBundle(
        quote("LQQ3.L", as_of, "100", "GBP", "GBX"),
        quote("ERNS.L", as_of, "50", "GBP", "GBP"),
        (
            quote("TQQQ", date(2025, 12, 31), "50", "USD"),
            quote("TQQQ", as_of, "60", "USD"),
        ),
        tuple(quote("SPY", day, "100", "USD") for day in prior) +
        (quote("SPY", as_of, "90", "USD"),))
    return history, prices


def audit_map(result):
    return {item.name: item.value for item in result.audit}


class PureEvaluationTests(TestCase):
    def test_irregular_cashflows_added_half_at_each_quarter_close(self):
        history, prices = sample()
        result = evaluate(history, prices)
        self.assertIn(result.status, ("ok", "action_needed"))
        self.assertEqual(result.strategy_version, VERSION)
        # 1000*1.09^2 + ((120+80)/2)*1.09 + (50/2) = 1322.10
        self.assertEqual(D(audit_map(result)["signal_line"]), D("1322.10"))
        self.assertEqual(D(audit_map(result)["equity_value"]), D("1000"))
        self.assertEqual(D(audit_map(result)["reserve"]), D("600"))
        self.assertTrue(any(x["kind"] == "QUARTERLY" for x in result.alerts))

    def test_spy_30down_raises_warning(self):
        history, prices = sample()
        spy = prices.spy[:-1] + (replace(prices.spy[-1], price=D("65"), raw_close=D("65")),)
        result = evaluate(history, replace(prices, spy=spy))
        self.assertIn(result.status, ("ok", "action_needed"))
        self.assertEqual(audit_map(result)["thirty_down_triggered"], "True")
        self.assertEqual(len([a for a in result.alerts
                              if a["kind"] == "THIRTY_DOWN"]), 1)

    def test_100_percent_tqqq_proxy_is_not_executable(self):
        history, prices = sample()
        tqqq = prices.tqqq[:-1] + (replace(prices.tqqq[-1], price=D("100"), raw_close=D("100")),)
        result = evaluate(history, replace(prices, tqqq=tqqq))
        self.assertIn(result.status, ("ok", "action_needed"))
        self.assertEqual(audit_map(result)["potential_spike"], "True")
        self.assertTrue(any(a["kind"] == "SPIKE_WATCH" for a in result.alerts))
        quarterly = next(a for a in result.alerts if a["kind"] == "QUARTERLY")
        self.assertEqual(quarterly["actions"], [])

    def test_unverified_gbx_gbp_and_rule_changes_refused(self):
        history, prices = sample()
        self.assertEqual(evaluate(history, replace(prices,
            equity=replace(prices.equity, unit="GBX"))).status, "cannot_compute")
        self.assertEqual(evaluate(history, replace(prices,
            equity=replace(prices.equity, raw_unit_verified=False))).status, "cannot_compute")
        self.assertEqual(evaluate(history, prices, replace(
            PARAMS, quarterly_growth=D("1.10"))).status, "cannot_compute")

    def test_incomplete_spy_lookback_refused(self):
        history, prices = sample()
        self.assertEqual(evaluate(history, replace(prices, spy=prices.spy[-2:])).status, "cannot_compute")


class IndependentReferenceTests(TestCase):
    @patch("tracker.core.prices.urlopen")
    def test_scale_corrobated_stooq_us_reference(self, mocked):
        payload = (b"Date,Open,High,Low,Close,Volume\n"
                   b"2026-03-30,60,61,59,60,200\n"
                   b"2026-03-31,61,62,60,61,100\n")
        response = MagicMock()
        response.status = 200
        response.read.return_value = payload
        mocked.return_value.__enter__.return_value = response
        trusted = quote("TQQQ", date(2026, 3, 30), "60", "USD")
        got = fetch_stooq_reference("TQQQ", date(2026, 3, 30),
                                    date(2026, 3, 31), yahoo_anchor=trusted)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[-1].price, D("61"))
        self.assertEqual(got[-1].source, "stooq")
        self.assertTrue(got[-1].raw_unit_verified)

    def test_stale_primary_reference_uses_corrobated_backup(self):
        anchor = quote("SPY", date(2026, 3, 30), "100", "USD")
        stale = quote("SPY", date(2026, 3, 1), "99", "USD")
        backup = (replace(anchor, source="stooq"),
                  quote("SPY", date(2026, 3, 31), "101", "USD", source="stooq"))
        with patch("tracker.core.prices.fetch_stooq_reference", return_value=backup):
            # Use an explicitly injected fetcher so source choice is testable.
            def fake_fetch(*args, **kwargs):
                return backup
            selected = reference_with_fallback(
                "SPY", date(2026, 3, 1), date(2026, 3, 31),
                yahoo_quotes=(stale,), yahoo_anchor=anchor,
                fallback_fetch=fake_fetch)
        self.assertEqual(selected[-1].source, "stooq")

    def test_uk_execution_ticker_refused_as_fallback(self):
        with self.assertRaises(QuoteUnavailable):
            fetch_stooq_reference("LQQ3.L", date(2026, 3, 30),
                date(2026, 3, 31),
                yahoo_anchor=quote("LQQ3.L", date(2026, 3, 30), "100", "GBP", "GBX"))

    @patch("tracker.core.prices.urlopen")
    def test_no_matching_yahoo_overlap_refused(self, mocked):
        response = MagicMock()
        response.status = 200
        response.read.return_value = b"Date,Close\n2026-03-31,9999\n"
        mocked.return_value.__enter__.return_value = response
        with self.assertRaises(QuoteUnavailable):
            fetch_stooq_reference("SPY", date(2026, 3, 30),
                date(2026, 3, 31),
                yahoo_anchor=quote("SPY", date(2026, 3, 30), "100", "USD"))


class OwnerSpreadsheetParityTests(TestCase):
    def _read(self, filename):
        with (FIXTURE_DIR / filename).open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))

    def test_six_quarter_owner_template_schema(self):
        rows = self._read("workbook_parity.csv")
        self.assertEqual(len(rows), 6)
        self.assertEqual(tuple(rows[0].keys()), PARITY_FIELDS)
        self.assertEqual([r["case_id"] for r in rows], [
            "Q1", "Q2", "Q3", "Q4", "Q5", "Q6"])
        self.assertTrue((FIXTURE_DIR / "workbook_quotes.csv").exists())
        self.assertTrue((FIXTURE_DIR / "workbook_contributions.csv").exists())

    def test_owner_sheet_parity_when_fixtures_supplied(self):
        rows = self._read("workbook_parity.csv")
        columns = PARITY_FIELDS[1:]
        if any(any(not row.get(col) for col in columns) for row in rows):
            self.skipTest("Owner worksheet values not yet supplied; no claimed real parity")
        quote_rows = self._read("workbook_quotes.csv")
        flow_rows = self._read("workbook_contributions.csv")
        for row in rows:
            key = row["case_id"]
            current = date.fromisoformat(row["quarter_end"])
            def number(field):
                return D(row[field])
            def yes(field):
                return row[field].lower() in ("true", "yes", "1")
            history = PortfolioHistory(
                date.fromisoformat(row["start_date"]), current,
                "LQQ3.L", "ERNS.L", number("equity_qty"),
                number("safe_qty"), number("cash_gbp"),
                number("starting_equity_gbp"),
                tuple(CashFlow(date.fromisoformat(f["transaction_date"]),
                    D(f["signed_external_flow_gbp"])) for f in flow_rows
                    if f["case_id"] == key),
                int(row["prior_skipped_sells"]), yes("prior_30down"),
                int(row["down_age_quarters"]))
            quotes = {}
            for q in quote_rows:
                if q["case_id"] == key:
                    sym = q["symbol"]
                    quotes.setdefault(sym, []).append(NormalizedQuote(
                        sym, date.fromisoformat(q["verified_close_date"]),
                        D(q["close_price"]), q["normalized_currency"],
                        q["normalized_currency"], q["source"],
                        q["raw_vendor_quote_unit"], True,
                        raw_close=(D(q["close_price"]) * (D("100") if
                            q["raw_vendor_quote_unit"] == "GBX" else D("1"))),
                        observation_id="owner:%s:%s:%s" % (key, sym, q["verified_close_date"])))
            prices = ApprovedPriceBundle(
                quotes["LQQ3.L"][-1], quotes["ERNS.L"][-1],
                tuple(quotes["TQQQ"]), tuple(quotes["SPY"]))
            actual = evaluate(history, prices)
            self.assertIn(actual.status, ("ok", "action_needed"), (key, actual.reason))
            audit = audit_map(actual)
            self.assertAlmostEqual(D(audit["signal_line"]),
                number("expected_signal_line_gbp"), delta=D("0.01"), msg=key)
            self.assertAlmostEqual(D(audit["equity_value"]),
                number("expected_equity_value_gbp"), delta=D("0.01"), msg=key)
            actions = [a for event in actual.alerts if event["kind"] == "QUARTERLY"
                       for a in event["actions"]]
            def total(side, symbol):
                return sum((D(a["quantity"]) for a in actions if
                    a["side"] == side and a["symbol"] == symbol), D("0"))
            expected = (
                ("BUY", "LQQ3.L", "expected_buy_equity_qty"),
                ("SELL", "LQQ3.L", "expected_sell_equity_qty"),
                ("BUY", "ERNS.L", "expected_buy_safe_qty"),
                ("SELL", "ERNS.L", "expected_sell_safe_qty"))
            for side, symbol, field in expected:
                self.assertAlmostEqual(total(side, symbol), number(field),
                    delta=D("0.0001"), msg=f"{key} {field}")
            self.assertEqual(audit["thirty_down_triggered"] == "True",
                yes("expected_30down"), key)
            self.assertEqual(audit["potential_spike"] == "True",
                yes("expected_spike_warning"), key)
            quarterly = next(a for a in actual.alerts if a["kind"] == "QUARTERLY")
            self.assertEqual("review a reset to 60%" in quarterly["message"],
                             yes("expected_reset_review"), key)
            est_cash = history.cash
            for a in actions:
                est_cash += (D(a["estimated_gbp"]) *
                    (D("1") if a["side"] == "SELL" else D("-1")))
            self.assertAlmostEqual(est_cash,
                number("expected_cash_after_estimated_orders_gbp"),
                delta=D("0.05"), msg=key)
