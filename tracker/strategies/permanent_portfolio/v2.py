"""Permanent Portfolio version 2: the version 1 band rule, checked once a year.

Pure: no database, network or clock. Harry Browne ("Fail-Safe Investing",
1999) says to look at the portfolio once a year: if any of the four parts is
worth less than 15% or more than 35% of the whole, restore 25% each;
otherwise do nothing. Version 1 checked after every month end; the rule
itself is unchanged, so this delegates to it and only the review schedule
(the plug-in's cadence: each 31 December) and the version differ.
Not affiliated with Harry Browne's estate or publishers.
"""
from dataclasses import replace

from tracker.strategies.permanent_portfolio import v1
from tracker.strategies.permanent_portfolio.v1 import (  # noqa: F401 (re-exported)
    BAND_HIGH, BAND_LOW, WEIGHTS, Fund, Quote,
)

VERSION = "permanent_portfolio.v2.0.0"


def evaluate(*, funds, positions, cash, quotes, as_of):
    """Rebalance to 25% each only when a part is outside 15-35% at ``as_of`` (a year end)."""
    return replace(v1.evaluate(funds=funds, positions=positions, cash=cash, quotes=quotes, as_of=as_of),
                   strategy_version=VERSION)
