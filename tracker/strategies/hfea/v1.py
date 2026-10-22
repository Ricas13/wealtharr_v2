"""HFEA version 1: 55% 3x S&P 500 and 45% 3x long Treasuries, rebalanced each quarter.

Pure: no database, network or clock. The published rules (HEDGEFUNDIE,
"HEDGEFUNDIE's excellent adventure", Bogleheads forum, 2019): hold UPRO
(3x daily S&P 500) and TMF (3x daily 20+ year US Treasuries), first 40/60,
then his own revision to 55/45, put back to those weights every quarter.
No bands. Not affiliated with HEDGEFUNDIE or the Bogleheads forum.
"""
from decimal import Decimal

from tracker.strategies.fixed_weights import Fund, Quote, rebalance  # noqa: F401 (re-exported)

VERSION = "hfea.v1.0.0"
WEIGHTS = (("stocks3x", Decimal("0.55")), ("bonds3x", Decimal("0.45")))


def evaluate(*, funds, positions, cash, quotes, as_of):
    """Trades restoring 55/45 at ``as_of`` (a calendar quarter end)."""
    return rebalance(version=VERSION, funds=funds, positions=positions, cash=cash,
                     quotes=quotes, as_of=as_of)
