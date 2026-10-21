"""Golden Butterfly version 1: five equal weights, rebalanced once a year.

Pure: no database, network or clock. The published mix (Tyler, Portfolio
Charts): 20% total stock market, 20% small-cap value, 20% long-term
government bonds, 20% short-term government bonds and 20% gold, put back to
those weights each year. Not affiliated with Portfolio Charts.
"""
from decimal import Decimal

from tracker.strategies.fixed_weights import Fund, Quote, rebalance  # noqa: F401 (re-exported)

VERSION = "golden_butterfly.v1.0.0"
WEIGHTS = (("stocks", Decimal("0.20")), ("scv", Decimal("0.20")), ("long", Decimal("0.20")),
           ("short", Decimal("0.20")), ("gold", Decimal("0.20")))


def evaluate(*, funds, positions, cash, quotes, as_of):
    """Trades restoring each fund's 20% weight at ``as_of`` (a year end)."""
    return rebalance(version=VERSION, funds=funds, positions=positions, cash=cash,
                     quotes=quotes, as_of=as_of)
