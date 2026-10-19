"""Versioned, code-defined descriptions used by all generic strategy pages.

These are read-only facts about the released strategy; no user/admin rule editor
and no implicit conversion from a mutable database StrategyPreset.
"""
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from tracker.strategies.nine_sig.v1 import VERSION as NINE_SIG_VERSION


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
            "Large buys are limited by the strategy's 81% buying-power safety throttle.",
            "The 30 Down reference uses SPY versus the previous eight quarter-end closes; the next two otherwise-required growth sells can be skipped.",
            "An eight-quarter expiry or third deferred sell requires a verified reset review, not an automatic trade.",
            "A 100%-up TQQQ reference event is a provisional manual-review warning until the actual reference is confirmed.",
            "Only owner-confirmed actual fills are recorded; ordinary quarterly trades do not create a 60/40 reset baseline.",
        ),
        role_targets=(("growth", Decimal("0.60")), ("safe", Decimal("0.40"))),
        target_explanation=("60/40 describes the initial or confirmed-reset mix. "
                            "It is NOT a standing allocation target every quarter."),
        caution=("LQQ3 is not TQQQ, and ERNS is not AGG. Spreadsheet parity and "
                 "your own Trading 212 ISA product permissions remain unverified."),
    ),
}
STRATEGIES = MappingProxyType(_RELEASED)


def describe_strategy(key: str) -> StrategyDescription | None:
    """Resolve a released description or refuse unknown mutable preset rules."""
    return STRATEGIES.get(key)
