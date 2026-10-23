"""Pure rules for the Nasdaq 3x trend + twelve-month DCA strategy.

No Django imports, network calls, broker actions, or inferred prices. The
caller supplies complete, adjusted, same-session daily SPY/QQQ observations.
The engine returns a target allocation only; separate services own fills.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

VERSION = "nasdaq_trend_dca.v1.0.0"
LOOKBACK = 200
ENTRY = Decimal("1.04")
EXIT = Decimal("0.97")
DELEVERAGE = Decimal("1.30")
CASH_GUARD = Decimal("1.40")
ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class Close:
    day: date
    spy: Decimal
    qqq: Decimal


@dataclass(frozen=True)
class State:
    """Committed state after the previous completed signal.

    off_since starts a new DCA schedule whenever SPY first exits.
    state is persisted per instance; no rewriting earlier signals.
    """
    regime: Literal["UNKNOWN", "ON", "OFF"] = "UNKNOWN"
    off_since: date | None = None


@dataclass(frozen=True)
class Decision:
    status: Literal["ok", "cannot_compute"]
    reason: str
    target: Literal["LEVERAGED", "UNLEVERAGED", "CASH", "DCA"] | None
    qqq_weight: Decimal | None
    leveraged_weight: Decimal | None
    cash_weight: Decimal | None
    state: State
    spy_ratio: Decimal | None = None
    qqq_ratio: Decimal | None = None


def _months_completed(start: date, end: date) -> int:
    if end < start:
        return 0
    n = (end.year - start.year) * 12 + end.month - start.month
    return max(0, min(12, n - (end.day < start.day)))


def decide(history: tuple[Close, ...], previous: State) -> Decision:
    """Use only completed sessions through the evaluation date.

    200 consecutive *observed US market sessions* are required. The caller
    must independently certify data freshness, source, adjustment, order and
    coverage; 200 arbitrary sparse observations are insufficient.
    """
    def refuse(msg):
        return Decision("cannot_compute", msg, None, None, None, None, previous)

    if len(history) < LOOKBACK:
        return refuse("A complete 200-session SPY and QQQ history is required.")
    window = history[-LOOKBACK:]
    if any(x.spy <= ZERO or x.qqq <= ZERO for x in window):
        return refuse("Reference prices must be positive.")
    if any(a.day >= b.day for a, b in zip(window, window[1:])):
        return refuse("Reference dates must be strictly increasing.")
    today = window[-1].day
    spy_average = sum((x.spy for x in window), ZERO) / LOOKBACK
    qqq_average = sum((x.qqq for x in window), ZERO) / LOOKBACK
    spy_ratio = window[-1].spy / spy_average
    qqq_ratio = window[-1].qqq / qqq_average

    regime = previous.regime
    off_since = previous.off_since
    if spy_ratio > ENTRY:
        regime, off_since = "ON", None
    elif spy_ratio < EXIT:
        if regime != "OFF":
            off_since = today
        regime = "OFF"
    if regime == "UNKNOWN":
        return refuse("Waiting for an explicit entry or exit threshold.")

    state = State(regime, off_since)
    if regime == "ON":
        if qqq_ratio >= CASH_GUARD:
            return Decision("ok", "QQQ 40% above SMA200: cash safeguard.", "CASH",
                            ZERO, ZERO, ONE, state, spy_ratio, qqq_ratio)
        if qqq_ratio >= DELEVERAGE:
            return Decision("ok", "QQQ 30% above SMA200: reduce to unleveraged Nasdaq.", "UNLEVERAGED",
                            ONE, ZERO, ZERO, state, spy_ratio, qqq_ratio)
        return Decision("ok", "SPY above the entry band: hold 3x Nasdaq.", "LEVERAGED",
                        ZERO, ONE, ZERO, state, spy_ratio, qqq_ratio)
    if off_since is None or off_since > today:
        return refuse("Exit state has no valid DCA start date.")
    n = _months_completed(off_since, today)
    weight = Decimal(n) / Decimal(12)
    return Decision("ok", "SPY below the exit band: twelve-month unleveraged DCA.",
                    "DCA", weight, ZERO, ONE - weight, state,
                    spy_ratio, qqq_ratio)
