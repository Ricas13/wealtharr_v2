"""Permanent Portfolio as a strategy plug-in: a band check after each month end."""
from tracker.strategies.permanent_portfolio import v1
from tracker.strategies.yearly_plugin import yearly_rebalance_plugin
from tracker.study_fixed import FixedWeightStudy, Source

KEY = "permanent-portfolio"
RULE = "rebalance all four to 25% only when one is below 15% or above 35%; otherwise do nothing."
# One Study per account currency, each with that account's funds.
STUDY = {
    "GBP": FixedWeightStudy(title="Permanent Portfolio", rules=v1, review_word="monthly", rule_text=RULE,
                            sources={
        "stocks": Source("VUAG.L", "US shares (VUAG)", "SPY", "SPY"),
        "long": Source("GLTL.L", "long gilts (GLTL)", "IGLT.L", "all-maturity gilts (IGLT)"),
        "cashfund": Source("ERNS.L", "sterling ultrashort bonds (ERNS)"),
        "gold": Source("SGLN.L", "gold (SGLN)", "GLD", "GLD"),
    }),
    "USD": FixedWeightStudy(title="Permanent Portfolio", rules=v1, review_word="monthly", rule_text=RULE,
                            currency="USD", sources={
        "stocks": Source("VTI", "US shares (VTI)"),
        "long": Source("TLT", "long Treasuries (TLT)"),
        "cashfund": Source("BIL", "T-bills (BIL)"),
        "gold": Source("GLD", "gold (GLD)"),
    }),
    "EUR": FixedWeightStudy(title="Permanent Portfolio", rules=v1, review_word="monthly", rule_text=RULE,
                            currency="EUR", sources={
        "stocks": Source("SXR8.DE", "US shares (SXR8)", "SPY", "SPY"),
        "long": Source("DBXF.DE", "long euro government bonds (DBXF)"),
        "cashfund": Source("XEON.DE", "euro cash (XEON)"),
        "gold": Source("PPFB.DE", "gold (PPFB)", "GLD", "GLD"),
    }),
}
PERMANENT_PORTFOLIO = yearly_rebalance_plugin(KEY, v1, study=STUDY, cadence="monthly")
