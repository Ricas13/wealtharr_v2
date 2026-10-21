"""All Weather as a strategy plug-in: one review after each year end."""
from tracker.strategies.all_weather import v1
from tracker.strategies.yearly_plugin import yearly_rebalance_plugin

KEY = "all-weather"
ALL_WEATHER = yearly_rebalance_plugin(KEY, v1)
