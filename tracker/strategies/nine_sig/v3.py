"""9Sig version 3: the v2 rules unchanged, for pound, dollar and euro accounts.

v2 accepts pound accounts only. v3 runs exactly the same pure v2 functions for
an account in GBP, USD or EUR, with every amount in that account's currency
(dollars: TQQQ and AGG, Kelly's own funds; euros: the Xetra lines). Pound
accounts keep v2; nothing about their rules or records changes.
"""
from tracker.strategies.nine_sig import v2
from tracker.strategies.nine_sig.v2 import (  # noqa: F401 (the same rules, re-exported)
    PARAMS, _drawdown_from_spy, _floor_qty, _order, _quarterly_closes, first_100_up_crossing,
    prior_quarter_name, quarter_end_before, quarter_number, signal_line_trace, signal_line_value,
)

VERSION = "nine_sig.v3.0.0"
CURRENCIES = ("GBP", "USD", "EUR")
SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}


def _relabel(text, currency):
    return text.replace("GBP", currency).replace("£", SYMBOLS[currency])


def advice_for_holdings(*, currency, **kwargs):
    """v2's quarterly advice for an account in ``currency``; [] for any other currency."""
    if currency not in CURRENCIES:
        return []
    rows = v2.advice_for_holdings(currency="GBP", **kwargs)
    if currency == "GBP":
        return rows
    # The rules are currency-free; only v2's wording names pounds.
    return [{k: (_relabel(v, currency) if isinstance(v, str) else v) for k, v in row.items()}
            for row in rows]
