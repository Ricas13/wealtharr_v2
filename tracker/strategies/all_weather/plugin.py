"""All Weather as a strategy plug-in: one review after each year end."""
from tracker.strategies.all_weather import v1
from tracker.strategies.yearly_plugin import yearly_rebalance_plugin
from tracker.study_fixed import FixedWeightStudy, Source

KEY = "all-weather"
STUDY = FixedWeightStudy(title="All Weather", rules=v1, sources={
    "stocks": Source("VUAG.L", "US shares (VUAG)", "SPY", "SPY"),
    "long": Source("GLTL.L", "long gilts (GLTL)", "IGLT.L", "all-maturity gilts (IGLT)"),
    "inter": Source("IGLT.L", "gilts (IGLT)"),
    "gold": Source("SGLN.L", "gold (SGLN)", "GLD", "GLD"),
    "comm": Source("CMOP.L", "commodities (CMOP)", "DBC", "DBC"),
})
ALL_WEATHER = yearly_rebalance_plugin(KEY, v1, study=STUDY)
