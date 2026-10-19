"""Workbook v1.07 DEMO 2010++: independent quarter-end v2 owner replay.

P2 (2010-05-07) is a *manual mid-quarter half-position sale*; excluded by
owner decision 2.  The twelve automatic reviews are P1 and P3--P13.
Workbook values below are comparison comments only, never engine targets.
"""
from datetime import date, timedelta
from decimal import Decimal as D

from django.test import SimpleTestCase

from tracker.market_signals import (
    _drawdown_from_spy, _quarterly_closes, advice_for_holdings,
    quarter_number, signal_line_value,
)
from tracker.strategies.nine_sig.v2 import (
    VERSION, ApprovedPriceBundle, NormalizedQuote, PortfolioHistory, evaluate,
)

# Ordered historical US closing observations supplied with the DEMO worksheet.
# TQQQ: growth reference only; SPY: the actual 30 Down rule reference.
OBSERVATIONS = (
    ("2010-02-11", "108.13", "0.40"),
    ("2010-03-31", "117.00", "0.29"),
    ("2010-05-07", "111.26", "0.24"),
    ("2010-06-30", "103.22", "0.19"),
    ("2010-09-30", "114.13", "0.28"),
    ("2010-12-31", "125.75", "0.39"),
    ("2011-03-31", "132.59", "0.44"),
    ("2011-06-30", "131.97", "0.43"),
    ("2011-09-30", "113.15", "0.31"),
    ("2011-12-30", "125.50", "0.35"),
    ("2012-03-30", "140.81", "0.62"),
    ("2012-06-29", "136.10", "0.52"),
    ("2012-09-28", "143.97", "0.63"),
    ("2012-12-31", "142.41", "0.54"),
)

# name, review, previous worksheet date, previous ending equity units,
# equity close, safe units, safe close, previous ending cash, cash deposited
# ON the review, previous signal line, current signal line, engine side/value.
CASES = (
    ("P1", "2010-03-31", "2010-02-11", "150000", "0.29", "382", "104.20",
     "69.54", "0", "60000.00", "65400.00", "BUY", "21899.99"),  # wb BUY 21899.93
    ("P3", "2010-06-30", "2010-05-07", "112758", "0.19", "431", "107.25",
     "71.58", "6000", "71286.00", "80701.74", "BUY", "52296.33"),  # wb BUY 52296.36 (whole shares)
    ("P4", "2010-09-30", "2010-06-30", "388002", "0.28", "0", "108.65",
     "0.00", "0", "80701.74", "87964.90", "SELL", "20675.66"),  # wb HOLD (manual 30% flag); workbook cash -0.03 rounded to 0.00
    ("P5", "2010-12-31", "2010-09-30", "388002", "0.39", "0", "105.75",
     "0.00", "0", "87964.8966", "95881.74", "SELL", "55439.04"),  # wb HOLD (manual 30% flag)
    ("P6", "2011-03-31", "2010-12-31", "388002", "0.44", "0", "105.13",
     "0.00", "0", "95881.7373", "104511.09", "SELL", "66209.79"),  # wb REBALANCE -68288.44, line 102432.53
    ("P7", "2011-06-30", "2011-03-31", "232801", "0.43", "649", "106.67",
     "59.07", "0", "102432.528", "111651.46", "BUY", "11547.02"),  # wb BUY 11547.22
    ("P8", "2011-09-30", "2011-06-30", "259655", "0.31", "541", "110.11",
     "32.21", "0", "111651.4555", "121700.09", "BUY", "41207.03"),  # wb BUY 41207.06
    ("P9", "2011-12-31", "2011-09-30", "392581", "0.35", "167", "110.25",
     "6.29", "0", "121700.0865", "132653.09", "SELL", "4750.26"),  # wb SELL -4750.20
    ("P10", "2012-03-31", "2011-12-31", "379009", "0.62", "210", "109.85",
     "15.74", "0", "132653.0943", "144591.87", "SELL", "90393.71"),  # wb SELL -90393.52
    ("P11", "2012-06-30", "2012-03-31", "233213", "0.52", "1033", "111.30",
     "2.71", "0", "144591.8728", "157605.14", "BUY", "36334.37"),  # wb BUY 36334.48
    ("P12", "2012-09-30", "2012-06-30", "303087", "0.63", "706", "112.45",
     "63.33", "0", "157605.1413", "171789.60", "SELL", "19155.21"),  # wb SELL -19155.15
    ("P13", "2012-12-31", "2012-09-30", "272682", "0.54", "876", "111.08",
     "101.98", "0", "171789.6041", "187250.67", "BUY", "40002.38"),  # wb BUY 40002.12
)


class WorkbookDemoReplay(SimpleTestCase):
    def test_every_completed_quarter_independently(self):
        # Never replay estimated fills forward: every case starts from the
        # supplied preceding workbook row, not another calculated output.
        self.assertEqual(len(CASES), 12)  # 13 worksheet periods less manual P2
        for (name, when, previous, equity, equity_close, safe, safe_close,
             cash, deposit, prior_line, expected_line, side, expected_value) in CASES:
            with self.subTest(period=name):
                as_of = date.fromisoformat(when)
                start_date = date.fromisoformat(previous) + timedelta(days=1)
                cash_flow = [(as_of, D(deposit))] if D(deposit) else []
                spy = [(date.fromisoformat(day), D(spy_close))
                       for day, spy_close, _ in OBSERVATIONS
                       if day <= when]
                tqqq = [(date.fromisoformat(day), D(tqqq_close))
                        for day, _, tqqq_close in OBSERVATIONS
                        if day <= when]
                line = signal_line_value(
                    start_date=start_date, as_of=as_of,
                    starting_equity_cost=D(prior_line),
                    contributions=cash_flow,
                )
                self.assertEqual(line, D(expected_line))
                down, _ = _drawdown_from_spy(
                    _quarterly_closes(spy, as_of), quarter_number(as_of))
                self.assertFalse(down, "SPY 30 Down must remain False")
                signals = advice_for_holdings(
                    start_date=start_date, as_of=as_of, currency="GBP",
                    equity_symbol="LQQ3.L", safe_symbol="ERNS.L",
                    equity_qty=D(equity), equity_price=D(equity_close),
                    safe_qty=D(safe), safe_price=D(safe_close),
                    cash=D(cash) + D(deposit),
                    starting_equity_cost=D(prior_line),
                    contributions=cash_flow,
                    reference=tqqq, spy_reference=spy,
                )
                q = next(s for s in signals if s["kind"] == "QUARTERLY")
                equity_orders = [a for a in q["actions"]
                                 if a["symbol"] == "LQQ3.L" and a["side"] in ("BUY", "SELL")]
                self.assertEqual(len(equity_orders), 1)
                self.assertEqual(equity_orders[0]["side"], side)
                self.assertLessEqual(
                    abs(D(equity_orders[0]["estimated_gbp"]) - D(expected_value)),
                    D("1.00"), "engine expected GBP amount differs by > £1")
                self.assertIn("SPY", q["message"])
                self.assertIn("Information only, doesn't affect the advice", q["message"])
                self.assertEqual(q["reset_reason"], "")
                if name == "P3":
                    # Decision 3: all 431 safe units liquidated, cash included.
                    safe_sales = [a for a in q["actions"]
                                  if a["symbol"] == "ERNS.L" and a["side"] == "SELL"]
                    self.assertEqual(len(safe_sales), 1)
                    self.assertEqual(D(safe_sales[0]["quantity"]), D("431"))
                    self.assertEqual(q["reserve_empty_review_notice"],
                                     "Your reserve is now empty. Review whether to reset to 60/40.")
                if name in ("P4", "P5", "P6"):
                    # Decision 1 intentionally rejects worksheet's hand-typed 30% flags.
                    self.assertEqual(side, "SELL")
                    self.assertNotIn("reset to 60%", q["message"])


def quote(symbol, day, value, currency, raw=None):
    price = D(value)
    unit = raw or currency
    return NormalizedQuote(
        symbol=symbol, as_of=day, price=price, currency=currency,
        unit=currency, source="yahoo", raw_unit=unit,
        raw_unit_verified=True,
        raw_close=price * (D("100") if unit == "GBX" else D("1")),
        observation_id="demo-audit:" + symbol + ":" + day.isoformat(),
    )


class OwnerV2AuditRegression(SimpleTestCase):
    def test_evaluation_audit_includes_informational_drawdown_and_no_reset(self):
        day = date(2026, 3, 31)
        history = PortfolioHistory(
            start_date=date(2026, 1, 1), as_of=day,
            equity_symbol="LQQ3.L", safe_symbol="ERNS.L",
            equity_qty=D("1"), safe_qty=D("1"), cash=D("0"),
            initial_equity_value=D("600"), strategy_version=VERSION,
        )
        spy_days = [date(y, m, d) for y, m, d in (
            (2024, 3, 31), (2024, 6, 30), (2024, 9, 30),
            (2024, 12, 31), (2025, 3, 31), (2025, 6, 30),
            (2025, 9, 30), (2025, 12, 31), (2026, 3, 31),
        )]
        for spy_current in ("100", "60"):
            with self.subTest(spy_current=spy_current):
                prices = ApprovedPriceBundle(
                    equity=quote("LQQ3.L", day, "10", "GBP", "GBX"),
                    safe=quote("ERNS.L", day, "100", "GBP"),
                    tqqq=(quote("TQQQ", date(2025, 12, 31), "100", "USD"),
                          quote("TQQQ", day, "50", "USD")),
                    spy=tuple(quote("SPY", d, spy_current if d == day else "100", "USD")
                              for d in spy_days),
                )
                result = evaluate(history, prices)
                self.assertEqual(result.status, "action_needed", result.reason)
                audit = result.audit_row()
                self.assertEqual(audit["growth_reference_drawdown_pct"], "50.00")
                self.assertEqual(audit["growth_drawdown_disclosure"],
                                 "Information only, doesn't affect the advice.")
                self.assertEqual(audit["rule_applied"], "All-in reserve shortage")
                self.assertEqual(audit["thirty_down_triggered"],
                                 str(spy_current == "60"))
                self.assertEqual(audit["reset_reason"], "")
                self.assertIn("Your reserve is now empty",
                              audit["reserve_empty_review_notice"])
                self.assertEqual(audit["throttle_81pct"], "False")
                self.assertEqual(audit["buy_budget"], "100")
                self.assertEqual(result.strategy_version, VERSION)
