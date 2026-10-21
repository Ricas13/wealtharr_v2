"""Versioned, code-defined descriptions used by all generic strategy pages.

These are read-only facts about the released strategy; no user/admin rule editor
and no implicit conversion from a mutable database StrategyPreset.
"""
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

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
}
STRATEGIES = MappingProxyType(_RELEASED)


def describe_strategy(key: str) -> StrategyDescription | None:
    """Resolve a released description or refuse unknown mutable preset rules."""
    return STRATEGIES.get(key)


# Plug-ins, keyed like the descriptions. Generic code finds a strategy's
# behaviour here and never imports a strategy's rules directly.
def _plugins():
    from tracker.strategies.nine_sig.plugin import NINE_SIG
    return {NINE_SIG.key: NINE_SIG}


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
