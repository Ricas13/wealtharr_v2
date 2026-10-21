"""Versioned, code-defined descriptions used by all generic strategy pages.

These are read-only facts about the released strategy; no user/admin rule editor
and no implicit conversion from a mutable database StrategyPreset.
"""
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from tracker.strategies.all_weather.v1 import VERSION as ALL_WEATHER_VERSION, WEIGHTS as ALL_WEATHER_WEIGHTS
from tracker.strategies.dual_momentum.v1 import VERSION as DUAL_MOMENTUM_VERSION
from tracker.strategies.golden_butterfly.v1 import (
    VERSION as GOLDEN_BUTTERFLY_VERSION, WEIGHTS as GOLDEN_BUTTERFLY_WEIGHTS,
)
from tracker.strategies.nine_sig.v2 import VERSION as NINE_SIG_VERSION


@dataclass(frozen=True)
class StrategyDescription:
    key: str
    version: str
    title: str
    summary: str
    cadence: str
    rules: tuple[str, ...]
    role_targets: tuple[tuple[str, Decimal], ...]
    target_explanation: str
    caution: str
    release_label: str = ""
    version_history: tuple[str, ...] = ()


_RELEASED = {
    "nasdaq-quarterly-target": StrategyDescription(
        key="nasdaq-quarterly-target",
        version=NINE_SIG_VERSION,
        title="9Sig quarterly target",
        summary=("Quarterly target-line tracking with a leveraged growth sleeve "
                 "and a defensive sleeve. All transactions are made by you."),
        cadence="Review completed calendar quarters; urgent events are review-only warnings.",
        rules=(
            "At the first purchase or a verified reset, the baseline is 60% growth and 40% defensive.",
            "The growth signal line rises by 9% each completed quarter, plus half the actual contributions made during that quarter.",
            "Trade proposals use the verified quarter-end holdings, GBP prices, cash and that quarter's target line.",
            "Shortfalls between 90%-100% of reserve use the 81% throttle; over-reserve buys exhaust it with an advisory 60/40 review.",
            "The 30 Down reference uses SPY versus the previous eight quarter-end closes; the next two otherwise-required growth sells can be skipped.",
            "An eight-quarter expiry or third deferred sell requires a verified reset review, not an automatic trade.",
            "A 100%-up TQQQ reference event is a provisional manual-review warning until the actual reference is confirmed.",
            "Only owner-confirmed actual fills are recorded; ordinary quarterly trades do not create a 60/40 reset baseline.",
        ),
        role_targets=(("growth", Decimal("0.60")), ("safe", Decimal("0.40"))),
        target_explanation=("60/40 describes the initial or confirmed-reset mix. "
                            "It is NOT a standing allocation target every quarter."),
        caution=("LQQ3 is not TQQQ, and ERNS is not AGG. Real-quarter workbook "
                 "parity and personal Trading 212 ISA permissions remain unverified."),
        release_label="nine-sig@2",
        version_history=(
            "nine-sig@2 (nine_sig.v2.0.0): all-in shortage, SPY 30 Down kept, "
            "TQQQ drawdown information only, no sell-half.",
            "nine-sig@1 (nine_sig.v1.0.0): frozen original with shortage reset/HOLD.",
        ),
    ),
    "all-weather": StrategyDescription(
        key="all-weather",
        version=ALL_WEATHER_VERSION,
        title="All Weather",
        summary=("The published All Weather mix, put back to its weights once a year: stocks, "
                 "long-term and intermediate government bonds, gold and broad commodities. "
                 "All transactions are made by you."),
        cadence="One review after each calendar year end.",
        rules=(
            "Target weights, as published: 30% US stocks, 40% long-term government bonds, 15% intermediate government bonds, 7.5% gold, 7.5% broad commodities.",
            "The original uses US Treasuries; in a UK account the bond roles use the UK equivalent, gilts. Each fund is shown with its evidence at setup.",
            "After each 31 December, holdings are valued at verified year-end GBP closes.",
            "Funds above their weight are sold back to it; the proceeds and any cash buy the funds below their weight.",
            "Buys never exceed the cash available; orders under £1 are skipped as too small to place.",
            "New money waits as cash until the next yearly review, or you can split it at the weights yourself.",
            "Only owner-confirmed actual fills are recorded. No trade is ever placed for you.",
        ),
        role_targets=ALL_WEATHER_WEIGHTS,
        target_explanation="These weights are restored at every yearly review.",
        caution=("Not affiliated with Bridgewater Associates. The rules are the published ones; the funds "
                 "are local equivalents, so results differ from the US original (gilts instead of US "
                 "Treasuries, an S&P 500 fund instead of the total US market). Your own account's "
                 "access to each fund is unverified."),
        release_label="all-weather@1",
        version_history=("all-weather@1 (all_weather.v1.0.0): yearly rebalance to fixed weights.",),
    ),
    "golden-butterfly": StrategyDescription(
        key="golden-butterfly",
        version=GOLDEN_BUTTERFLY_VERSION,
        title="Golden Butterfly",
        summary=("The published Golden Butterfly mix, five equal parts put back to 20% each once a "
                 "year: total stock market, small-cap value, long-term and short-term government "
                 "bonds, and gold. All transactions are made by you."),
        cadence="One review after each calendar year end.",
        rules=(
            "Target weights, as published: 20% US total stock market, 20% US small-cap value, 20% long-term government bonds, 20% short-term government bonds, 20% gold.",
            "The original uses US Treasuries; in a UK account the bond roles use the UK equivalent, gilts. Each fund is shown with its evidence at setup.",
            "After each 31 December, holdings are valued at verified year-end GBP closes.",
            "Funds above their weight are sold back to it; the proceeds and any cash buy the funds below their weight.",
            "Buys never exceed the cash available; orders under £1 are skipped as too small to place.",
            "New money waits as cash until the next yearly review, or you can split it at the weights yourself.",
            "Only owner-confirmed actual fills are recorded. No trade is ever placed for you.",
        ),
        role_targets=GOLDEN_BUTTERFLY_WEIGHTS,
        target_explanation="These weights are restored at every yearly review.",
        caution=("Not affiliated with Portfolio Charts. The rules are the published ones; the funds are "
                 "local equivalents, so results differ from the US original (gilts instead of US "
                 "Treasuries, an S&P 500 fund instead of the total US market). Your own account's "
                 "access to each fund is unverified."),
        release_label="golden-butterfly@1",
        version_history=("golden-butterfly@1 (golden_butterfly.v1.0.0): yearly rebalance to five equal weights.",),
    ),
    "dual-momentum": StrategyDescription(
        key="dual-momentum",
        version=DUAL_MOMENTUM_VERSION,
        title="Dual Momentum",
        summary=("Global Equities Momentum as published: each month, hold 100% of whichever is "
                 "strongest of US shares and world shares outside the US, or bonds when shares "
                 "aren't beating cash. All transactions are made by you."),
        cadence="One review after each calendar month end.",
        rules=(
            "At each month end, compare 12-month total returns from verified month-end closes.",
            "Absolute momentum: if US shares returned more than cash, stay in shares; otherwise hold bonds.",
            "Relative momentum: in shares, hold US shares or world ex-US shares, whichever returned more.",
            "Everything goes into the chosen fund: sell any other fund, then buy it with the proceeds and your cash.",
            "The original compares US-dollar returns with US Treasury bills and holds US aggregate bonds; in a UK account the returns are in pounds, cash is a sterling overnight-rate fund and the bond fund is the local equivalent. Each fund is shown with its evidence at setup.",
            "New money is invested in the chosen fund at the next monthly review. Orders under £1 are skipped as too small to place.",
            "Only owner-confirmed actual fills are recorded. No trade is ever placed for you.",
        ),
        role_targets=(("us", Decimal("0")), ("intl", Decimal("0")), ("bonds", Decimal("0"))),
        target_explanation="No fixed weights: 100% sits in the one fund the monthly rules choose.",
        caution=("Not affiliated with Gary Antonacci. The rules are the published ones; the funds and the "
                 "cash series are local equivalents, so switches can differ from the US original. "
                 "Momentum switches can lag sharp turns and trigger capital gains outside an ISA. "
                 "Your own account's access to each fund is unverified."),
        release_label="dual-momentum@1",
        version_history=("dual-momentum@1 (dual_momentum.v1.0.0): monthly Global Equities Momentum.",),
    ),
}
STRATEGIES = MappingProxyType(_RELEASED)


def describe_strategy(key: str) -> StrategyDescription | None:
    """Resolve a released description or refuse unknown mutable preset rules."""
    return STRATEGIES.get(key)


# Plug-ins, keyed like the descriptions. Generic code finds a strategy's
# behaviour here and never imports a strategy's rules directly.
def _plugins():
    from tracker.strategies.all_weather.plugin import ALL_WEATHER
    from tracker.strategies.dual_momentum.plugin import DUAL_MOMENTUM
    from tracker.strategies.golden_butterfly.plugin import GOLDEN_BUTTERFLY
    from tracker.strategies.nine_sig.plugin import NINE_SIG
    return {p.key: p for p in (NINE_SIG, ALL_WEATHER, GOLDEN_BUTTERFLY, DUAL_MOMENTUM)}


_MODULES = {}
_EXTRA = {}  # Test-only registrations; never used in production code paths.


def _all():
    if not _MODULES:
        _MODULES.update(_plugins())
    return {**_MODULES, **_EXTRA}


def module_for_key(key):
    return _all().get(key)


def module_for(instance):
    """The plug-in for a strategy instance, or None for an unknown strategy."""
    return module_for_key(getattr(instance, "strategy_key", "") or instance.preset.slug)


def plugin_keys():
    return tuple(_all())


def last_review_before(instance, day):
    """The strategy's last completed review date before ``day``."""
    from tracker.core.calendar import quarter_end_before
    plugin = module_for(instance)
    return (plugin.last_review_before if plugin else quarter_end_before)(day)


def next_review(instance, day):
    from tracker.core.calendar import quarter_end_on_or_after
    plugin = module_for(instance)
    return (plugin.next_review if plugin else quarter_end_on_or_after)(day)
