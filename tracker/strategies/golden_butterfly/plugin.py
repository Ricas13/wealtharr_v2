"""Golden Butterfly as a strategy plug-in: one review after each year end."""
from tracker.strategies.golden_butterfly import v1
from tracker.strategies.yearly_plugin import yearly_rebalance_plugin

KEY = "golden-butterfly"
GOLDEN_BUTTERFLY = yearly_rebalance_plugin(KEY, v1)
