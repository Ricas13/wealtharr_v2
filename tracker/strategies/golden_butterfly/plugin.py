"""Golden Butterfly as a strategy plug-in: one review after each year end."""
from tracker.strategies.golden_butterfly import v1
from tracker.strategies.yearly_plugin import yearly_rebalance_plugin
from tracker.study_fixed import FixedWeightStudy, Source

KEY = "golden-butterfly"
STUDY = FixedWeightStudy(title="Golden Butterfly", rules=v1, sources={
    "stocks": Source("VUAG.L", "US shares (VUAG)", "SPY", "SPY"),
    "scv": Source("USSC.L", "US small-cap value (USSC)", "VBR", "VBR"),
    "long": Source("GLTL.L", "long gilts (GLTL)", "IGLT.L", "all-maturity gilts (IGLT)"),
    "short": Source("IGLS.L", "short gilts (IGLS)"),
    "gold": Source("SGLN.L", "gold (SGLN)", "GLD", "GLD"),
})
GOLDEN_BUTTERFLY = yearly_rebalance_plugin(KEY, v1, study=STUDY)
