"""All Weather as a strategy plug-in: one review after each year end."""
from tracker.strategies.all_weather import v1
from tracker.strategies.yearly_plugin import yearly_rebalance_plugin
from tracker.study_fixed import FixedWeightStudy, Source

KEY = "all-weather"
# One Study per account currency, each with that account's funds.
STUDY = {
    "GBP": FixedWeightStudy(title="All Weather", rules=v1, sources={
        "stocks": Source("VUAG.L", "US shares (VUAG)", "SPY", "SPY"),
        "long": Source("GLTL.L", "long gilts (GLTL)", "IGLT.L", "all-maturity gilts (IGLT)"),
        "inter": Source("IGLT.L", "gilts (IGLT)"),
        "gold": Source("SGLN.L", "gold (SGLN)", "GLD", "GLD"),
        "comm": Source("CMOP.L", "commodities (CMOP)", "DBC", "DBC"),
    }),
    "USD": FixedWeightStudy(title="All Weather", currency="USD", rules=v1, sources={
        "stocks": Source("VTI", "US shares (VTI)"),
        "long": Source("TLT", "long Treasuries (TLT)"),
        "inter": Source("IEF", "intermediate Treasuries (IEF)"),
        "gold": Source("GLD", "gold (GLD)"),
        "comm": Source("DBC", "commodities (DBC)"),
    }),
    "EUR": FixedWeightStudy(title="All Weather", currency="EUR", rules=v1, sources={
        "stocks": Source("SXR8.DE", "US shares (SXR8)", "SPY", "SPY"),
        "long": Source("DBXF.DE", "long euro government bonds (DBXF)"),
        "inter": Source("SXRQ.DE", "7-10 year euro government bonds (SXRQ)"),
        "gold": Source("PPFB.DE", "gold (PPFB)", "GLD", "GLD"),
        "comm": Source("SXRS.DE", "commodities (SXRS)", "DBC", "DBC"),
    }),
}
ALL_WEATHER = yearly_rebalance_plugin(KEY, v1, study=STUDY)
