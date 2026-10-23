"""Pure, deterministic SPY-200SMA / TQQQ / QQQ strategy rules.

Faithfulness:
- Original Reddit post (2025-09-15): https://www.reddit.com/r/LETFs/comments/1nhye66/
- Author's clarification (2026-03-27 thread): after the +30/+40% QQQ
  'bodyguard' activates, remain de-leveraged until a full SPY exit and
  subsequent entry; do NOT re-leverage simply when QQQ cools off.
- The author says DCA over 6-12 months and specifically proposes twelve
  monthly cash-funded QQQ purchases. The v1 *implementation choice* is 12.

This module does not fetch prices, place trades, assume fills, or infer cash
balances. The application must verify 200 aligned, consecutive US trading
sessions, completed chart-compatible closes and quote provenance; persist each
returned State. The author uses TradingView `close` on SPY/QQQ (not a
dividend-reinvested total-return series); a different data basis requires
independent threshold parity tests. Generate DCA orders only from confirmed
proceeds, subtracting recorded
purchases rather than continuously rebalancing market-value weights.
"""
from calendar import monthrange
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
DCA_INSTALMENTS = 12
DCA_FRACTION = ONE / Decimal(DCA_INSTALMENTS)

Regime = Literal["UNKNOWN", "ON", "OFF"]
Guard = Literal["NONE", "UNLEVERAGED", "CASH"]
Target = Literal["LEVERAGED", "UNLEVERAGED", "CASH", "DCA"]


@dataclass(frozen=True)
class Close:
    day: date
    spy: Decimal
    qqq: Decimal


@dataclass(frozen=True)
class State:
    """Last confirmed signal state, never derived from today's holdings.

    guard latches until SPY has exited BELOW -3%, then re-entered ABOVE +4%.
    off_since anchors the DCA schedule; each trade is confirmed separately.
    """
    regime: Regime = "UNKNOWN"
    off_since: date | None = None
    guard: Guard = "NONE"


@dataclass(frozen=True)
class Decision:
    status: Literal["ok", "cannot_compute"]
    reason: str
    target: Target | None
    qqq_weight: Decimal | None
    leveraged_weight: Decimal | None
    cash_weight: Decimal | None
    state: State
    spy_ratio: Decimal | None = None
    qqq_ratio: Decimal | None = None
    dca_installments_due: int = 0
    dca_fraction_per_installment: Decimal | None = None


def _months_completed(start: date, end: date) -> int:
    """Month-end-aware anniversaries, capped at 12 months.

    January 31 -> February 28 (or 29) counts as one monthly instalment.
    Non-trading due dates are handled on the next eligible trading session.
    """
    if end < start:
        return 0
    elapsed = (end.year - start.year) * 12 + end.month - start.month
    anniversary = date(end.year, end.month, min(start.day, monthrange(end.year, end.month)[1]))
    if end < anniversary:
        elapsed -= 1
    return max(0, min(DCA_INSTALMENTS, elapsed))


def decide(
    history: tuple[Close, ...],
    previous: State,
    *,
    filled_dca_installments: int = 0,
) -> Decision:
    """Evaluate a completed US market session using the trailing 200 closes.

    filled_dca_installments MUST be reconciled against real, confirmed QQQ
    instalments belonging to the *current* off_since cycle (not assumed
    because time passed). This only reports due instalments, NOT instructions
    to place 1/12 of the CURRENT account value or to rebalance QQQ shares.
    """
    def refuse(msg: str) -> Decision:
        return Decision("cannot_compute", msg, None, None, None, None, previous)

    if not isinstance(filled_dca_installments, int) or isinstance(filled_dca_installments, bool):
        return refuse("Confirmed instalment count must be an integer.")
    if not 0 <= filled_dca_installments <= DCA_INSTALMENTS:
        return refuse("Confirmed instalments must be between zero and twelve.")
    if previous.regime not in ("UNKNOWN", "ON", "OFF") or previous.guard not in (
        "NONE", "UNLEVERAGED", "CASH"
    ):
        return refuse("Unknown persisted SPY regime or safeguard state.")
    if previous.regime == "OFF" and previous.off_since is None:
        return refuse("Persisted exit state has no DCA start date.")
    if previous.regime != "OFF" and previous.off_since is not None:
        return refuse("An active/unknown regime cannot retain an old DCA start date.")
    if previous.regime != "ON" and previous.guard != "NONE":
        return refuse("A safeguard may be latched only in the active SPY regime.")
    if len(history) < LOOKBACK:
        return refuse("A complete 200-session SPY and QQQ history is required.")
    window = history[-LOOKBACK:]
    if any(not x.spy.is_finite() or not x.qqq.is_finite() or x.spy <= ZERO or x.qqq <= ZERO for x in window):
        return refuse("Reference prices must be positive and finite.")
    if any(a.day >= b.day for a, b in zip(window, window[1:])):
        return refuse("Reference dates must be strictly increasing.")

    today = window[-1].day
    spy_average = sum((x.spy for x in window), ZERO) / LOOKBACK
    qqq_average = sum((x.qqq for x in window), ZERO) / LOOKBACK
    spy_ratio = window[-1].spy / spy_average
    qqq_ratio = window[-1].qqq / qqq_average

    regime, off_since, guard = previous.regime, previous.off_since, previous.guard
    if spy_ratio < EXIT:
        # Full SELL threshold resets any old bubble guard.
        if regime != "OFF":
            off_since = today
        regime, guard = "OFF", "NONE"
    elif spy_ratio > ENTRY:
        # Only an OFF -> ON entry clears the old guard, which should already
        # have been cleared by the preceding SELL. Staying ON never clears it.
        if regime != "ON":
            guard = "NONE"
        regime, off_since = "ON", None

    if regime == "UNKNOWN":
        return refuse("Waiting for an explicit SPY entry or exit threshold.")

    if regime == "ON":
        if qqq_ratio >= CASH_GUARD:
            guard = "CASH"
        elif qqq_ratio >= DELEVERAGE and guard == "NONE":
            guard = "UNLEVERAGED"
        state = State(regime, None, guard)
        if guard == "CASH":
            return Decision("ok", "QQQ bodyguard active: remain in cash until a new SPY sell/buy cycle.",
                            "CASH", ZERO, ZERO, ONE, state, spy_ratio, qqq_ratio)
        if guard == "UNLEVERAGED":
            return Decision("ok", "QQQ bodyguard active: remain in unleveraged Nasdaq until a new SPY sell/buy cycle.",
                            "UNLEVERAGED", ONE, ZERO, ZERO, state, spy_ratio, qqq_ratio)
        return Decision("ok", "SPY above the entry band: hold 3x Nasdaq.", "LEVERAGED",
                        ZERO, ONE, ZERO, state, spy_ratio, qqq_ratio)

    if off_since is None or off_since > today:
        return refuse("Exit state has no valid DCA start date.")
    if previous.regime == "OFF" and filled_dca_installments > _months_completed(off_since, today):
        return refuse("Recorded DCA purchases exceed the elapsed instalment schedule.")

    due = max(0, _months_completed(off_since, today) - filled_dca_installments)
    # No fixed allocation targets while DCA-ing: price changes must NEVER
    # cause automatic selling/rebuying of the existing QQQ holdings.
    return Decision(
        "ok",
        "SPY exit active: hold sale proceeds as cash and buy QQQ in twelve monthly instalments. "
        "Each instalment is 1/12 of the confirmed original cash reserve, not a portfolio rebalance.",
        "DCA", None, ZERO, None, State("OFF", off_since, "NONE"),
        spy_ratio, qqq_ratio, due, DCA_FRACTION,
    )
