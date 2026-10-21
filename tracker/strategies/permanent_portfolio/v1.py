"""Permanent Portfolio version 1: four equal parts, rebalanced only outside 15-35%.

Pure: no database, network or clock. The published rules (Harry Browne,
"Fail-Safe Investing", 1999): 25% each in stocks, long-term government bonds,
cash (Treasury bills) and gold. When any part falls below 15% or rises above
35% of the portfolio, put all four back to 25%. Otherwise do nothing.
Not affiliated with Harry Browne's estate or publishers.
"""
from decimal import Decimal

from tracker.strategies.fixed_weights import ZERO, Fund, Quote, Result, rebalance  # noqa: F401

VERSION = "permanent_portfolio.v1.0.0"
WEIGHTS = (("stocks", Decimal("0.25")), ("long", Decimal("0.25")),
           ("cashfund", Decimal("0.25")), ("gold", Decimal("0.25")))
BAND_LOW = Decimal("0.15")
BAND_HIGH = Decimal("0.35")


def evaluate(*, funds, positions, cash, quotes, as_of):
    """Rebalance to 25% each only when a part is outside its 15-35% band at ``as_of``."""
    if any(f.symbol not in quotes or quotes[f.symbol].price_gbp <= ZERO for f in funds):
        return rebalance(version=VERSION, funds=funds, positions=positions, cash=cash,
                         quotes=quotes, as_of=as_of)  # the shared checks give the reason
    values = {f: positions.get(f.symbol, ZERO) * quotes[f.symbol].price_gbp for f in funds}
    total = sum(values.values(), ZERO) + cash
    if total > ZERO and cash >= ZERO and all(q >= ZERO for q in positions.values()):
        outside = [f for f in funds if not BAND_LOW <= values[f] / total <= BAND_HIGH]
        if not outside:
            return Result(VERSION, "ok", "WITHIN_BANDS",
                          "Every part is between 15% and 35%. Nothing to do.",
                          audit=tuple(("%s_weight" % f.role, str((values[f] / total).quantize(Decimal("0.0001"))))
                                      for f in funds))
    result = rebalance(version=VERSION, funds=funds, positions=positions, cash=cash,
                       quotes=quotes, as_of=as_of)
    if result.status != "action_needed":
        return result
    return Result(VERSION, result.status, "BAND_BREACH",
                  "A part is outside 15-35%: put all four back to 25%. " + result.reason,
                  trades=result.trades, audit=result.audit)
