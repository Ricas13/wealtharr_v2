"""Permanent Portfolio as a strategy plug-in: a band check after each year end.

Version 2 (new instances) checks once a year, as Browne wrote. Instances
pinned to version 1 keep its monthly check until they are explicitly
upgraded, so their saved reviews stay reproducible.
"""
from tracker.strategies.permanent_portfolio import v1, v2
from tracker.strategies.yearly_plugin import yearly_rebalance_plugin
from tracker.study_fixed import FixedWeightStudy, Source

KEY = "permanent-portfolio"
RULE = "rebalance all four to 25% only when one is below 15% or above 35%; otherwise do nothing."
# Each account currency's funds; stand-ins before a fund existed.
SOURCES = {
    "GBP": {
        "stocks": Source("VUAG.L", "US shares (VUAG)", "SPY", "SPY"),
        "long": Source("GLTL.L", "long gilts (GLTL)", "IGLT.L", "all-maturity gilts (IGLT)"),
        "cashfund": Source("ERNS.L", "sterling ultrashort bonds (ERNS)"),
        "gold": Source("SGLN.L", "gold (SGLN)", "GLD", "GLD"),
    },
    "USD": {
        "stocks": Source("VTI", "US shares (VTI)"),
        "long": Source("TLT", "long Treasuries (TLT)"),
        "cashfund": Source("BIL", "T-bills (BIL)"),
        "gold": Source("GLD", "gold (GLD)"),
    },
    "EUR": {
        "stocks": Source("SXR8.DE", "US shares (SXR8)", "SPY", "SPY"),
        "long": Source("DBXF.DE", "long euro government bonds (DBXF)"),
        "cashfund": Source("XEON.DE", "euro cash (XEON)"),
        "gold": Source("PPFB.DE", "gold (PPFB)", "GLD", "GLD"),
    },
}


def _studies(rules, review_word):
    """One Study per account currency, replaying ``rules`` on its schedule."""
    return {currency: FixedWeightStudy(title="Permanent Portfolio", rules=rules, review_word=review_word,
                                       rule_text=RULE, currency=currency, sources=sources)
            for currency, sources in SOURCES.items()}


STUDY = _studies(v2, "yearly")
PERMANENT_PORTFOLIO = yearly_rebalance_plugin(KEY, v2, study=STUDY, cadence="yearly", by_version={
    v1.VERSION: yearly_rebalance_plugin(KEY, v1, study=_studies(v1, "monthly"), cadence="monthly"),
})
