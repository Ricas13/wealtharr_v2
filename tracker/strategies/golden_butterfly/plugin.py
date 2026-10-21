"""Golden Butterfly as a strategy plug-in: one review after each year end."""
from tracker.strategies.golden_butterfly import v1
from tracker.strategies.yearly_plugin import yearly_rebalance_plugin
from tracker.study_fixed import FixedWeightStudy, Source

KEY = "golden-butterfly"
# One Study per account currency, each with that account's funds.
STUDY = {
    "GBP": FixedWeightStudy(title="Golden Butterfly", rules=v1, sources={
        "stocks": Source("VUAG.L", "US shares (VUAG)", "SPY", "SPY"),
        "scv": Source("USSC.L", "US small-cap value (USSC)", "VBR", "VBR"),
        "long": Source("GLTL.L", "long gilts (GLTL)", "IGLT.L", "all-maturity gilts (IGLT)"),
        "short": Source("IGLS.L", "short gilts (IGLS)"),
        "gold": Source("SGLN.L", "gold (SGLN)", "GLD", "GLD"),
    }),
    "USD": FixedWeightStudy(title="Golden Butterfly", currency="USD", rules=v1, sources={
        "stocks": Source("VTI", "US shares (VTI)"),
        "scv": Source("VBR", "US small-cap value (VBR)"),
        "long": Source("TLT", "long Treasuries (TLT)"),
        "short": Source("SHY", "short Treasuries (SHY)"),
        "gold": Source("GLD", "gold (GLD)"),
    }),
    "EUR": FixedWeightStudy(title="Golden Butterfly", currency="EUR", rules=v1, sources={
        "stocks": Source("SXR8.DE", "US shares (SXR8)", "SPY", "SPY"),
        "scv": Source("ZPRV.DE", "US small-cap value (ZPRV)", "VBR", "VBR"),
        "long": Source("DBXF.DE", "long euro government bonds (DBXF)"),
        "short": Source("DBXP.DE", "short euro government bonds (DBXP)"),
        "gold": Source("PPFB.DE", "gold (PPFB)", "GLD", "GLD"),
    }),
}
GOLDEN_BUTTERFLY = yearly_rebalance_plugin(KEY, v1, study=STUDY)
