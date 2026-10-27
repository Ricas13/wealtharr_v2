"""Nasdaq Trend + DCA v1. Pure, versioned end-of-day strategy.

No Django, broker, prices fetched or simulated fills. Reference signals are
SPY/QQQ in USD; trades use the account's pinned actual trading lines.

Published rules (r/LETFs "SPY 200SMA +4%/-3% -> TQQQ/QQQ"): SPY close above
104% of its 200-session average -> 3x Nasdaq; below 97% -> sell to cash and
buy 1x Nasdaq in twelve monthly instalments. While SPY is on, QQQ at or above
130% of its own average -> 1x Nasdaq, at or above 140% -> cash. The QQQ
guard stays on until SPY has exited and re-entered (the author's later
clarification), so the full close history is replayed, never a trailing window.
"""
from dataclasses import dataclass
from datetime import date
from calendar import monthrange
from decimal import Decimal, ROUND_DOWN

from tracker.strategies.fixed_weights import Quote, Result, Trade

VERSION = "nasdaq_trend_dca.v1.0.0"
ROLES = ("leveraged", "unleveraged")
WINDOW = 200
ENTER = Decimal("1.04")
EXIT = Decimal("0.97")
REDUCE = Decimal("1.30")
CASH_GUARD = Decimal("1.40")
ZERO = Decimal("0")
ONE = Decimal("1")
CENT = Decimal("0.01")
SHARE = Decimal("0.0001")
MIN_TRADE = Decimal("1")
INSTALMENTS = 12


@dataclass(frozen=True)
class TrendSignal:
    as_of: date
    mode: str  # leveraged, unleveraged, cash, dca
    regime: str  # on, off
    guard: str  # none, unleveraged, cash (only while regime is on)
    exit_on: date | None
    spy_ratio: Decimal
    qqq_ratio: Decimal


def elapsed_months(start, end):
    """Completed monthly anniversaries from ``start`` to ``end``, capped at 12.

    Month-end aware: 31 January to 28 February counts as one month.
    """
    if not start or end < start:
        return 0
    count = (end.year - start.year) * 12 + end.month - start.month
    if end.day < min(start.day, monthrange(end.year, end.month)[1]):
        count -= 1
    return min(INSTALMENTS, max(0, count))


def trend_series(spy, qqq, *, enter=ENTER, exit_=EXIT, reduce=REDUCE, cash_guard=CASH_GUARD):
    """Daily state over the full shared history, in O(n).

    The average uses the 200 closes ending on the day itself (so a day never
    sees a later close). Days before the first SPY band crossing have no state.
    The thresholds default to the released rules; only the hypothetical Study
    simulator passes others.
    """
    sp = {d: Decimal(str(p)) for d, p in spy if Decimal(str(p)) > ZERO}
    qp = {d: Decimal(str(p)) for d, p in qqq if Decimal(str(p)) > ZERO}
    days = sorted(set(sp) & set(qp))
    if len(days) < WINDOW:
        return {}
    regime, guard, exit_on, out = None, "none", None, {}
    sp_sum = sum((sp[d] for d in days[:WINDOW]), ZERO)
    q_sum = sum((qp[d] for d in days[:WINDOW]), ZERO)
    for i in range(WINDOW - 1, len(days)):
        if i >= WINDOW:
            sp_sum += sp[days[i]] - sp[days[i - WINDOW]]
            q_sum += qp[days[i]] - qp[days[i - WINDOW]]
        spy_ratio = sp[days[i]] * WINDOW / sp_sum
        qqq_ratio = qp[days[i]] * WINDOW / q_sum
        if spy_ratio > enter:
            if regime != "on":
                guard = "none"
            regime, exit_on = "on", None
        elif spy_ratio < exit_:
            if regime != "off":
                exit_on = days[i]
            regime, guard = "off", "none"
        if regime is None:
            continue
        if regime == "on":
            if qqq_ratio >= cash_guard:
                guard = "cash"
            elif qqq_ratio >= reduce and guard == "none":
                guard = "unleveraged"
            mode = {"none": "leveraged", "unleveraged": "unleveraged", "cash": "cash"}[guard]
        else:
            mode = "dca"
        out[days[i]] = TrendSignal(days[i], mode, regime, guard, exit_on, spy_ratio, qqq_ratio)
    return out


def trend_at(spy, qqq, as_of):
    """The most recent state using only closes up to ``as_of``."""
    result = trend_series(
        ((d, p) for d, p in spy if d <= as_of),
        ((d, p) for d, p in qqq if d <= as_of))
    return result[max(result)] if result else None


def dca_target(inflows, exit_on, as_of):
    """Cash that should have been invested by ``as_of``: each inflow (opening
    cash, sale proceeds, deposits, dividends) is released in twelve equal monthly
    steps from its own date, or from the exit if that is later.
    """
    return sum((amount * elapsed_months(max(day, exit_on), as_of)
                for day, amount in inflows), ZERO) / INSTALMENTS


def evaluate(*, funds, positions, cash, quotes, signal, inflows=(), dca_spent=ZERO):
    """Recommend owner-confirmed fills; never place trades or fabricate holdings.

    ``inflows`` are the dated cash additions since the SPY exit and ``dca_spent``
    what was actually invested in 1x Nasdaq since then, both read from the
    ledger by the caller. Orders are for the next session.
    """
    def refuse(code, reason):
        return Result(VERSION, "cannot_compute", code, reason)
    if signal is None:
        return refuse("HISTORY", "No settled 200-session SPY/QQQ signal is available.")
    if set(funds) != set(ROLES) or len(set(funds.values())) != len(ROLES):
        return refuse("ROLES", "Two distinct mapped funds are required.")
    if cash < ZERO or dca_spent < ZERO or any(q < ZERO for q in positions.values()):
        return refuse("NEGATIVE", "Negative cash or holdings require reconciliation.")
    if any(s not in funds.values() for s, q in positions.items() if q > ZERO):
        return refuse("OUTSIDE", "A holding is outside the strategy's two funds.")
    if any(k not in quotes or quotes[k].price_gbp <= ZERO for k in funds.values()):
        return refuse("PRICE", "Both mapped instruments need approved account-currency closes.")

    mode = signal.mode
    held = {r: positions.get(s, ZERO) for r, s in funds.items()}
    if mode in ("leveraged", "unleveraged"):
        sale_roles = [r for r in ROLES if r != mode]
    elif mode == "cash":
        sale_roles = list(ROLES)
    elif mode == "dca":
        # The exit sells everything. Once instalments have begun, 1x Nasdaq is
        # what the instalments bought, so only 3x is ever sold.
        sale_roles = ["leveraged"] if dca_spent > ZERO else list(ROLES)
    else:
        return refuse("MODE", "Unrecognised strategy mode.")
    sells = []
    for role in sale_roles:
        qty = held[role]
        if qty <= ZERO:
            continue
        quote = quotes[funds[role]]
        sells.append(Trade(role, funds[role], "SELL", qty,
                           (qty * quote.price_gbp).quantize(CENT), quote.price_gbp,
                           quote.as_of, "Follow the SPY trend exit or QQQ guard."))

    buys, chosen, amount, reason = [], None, ZERO, ""
    if mode in ("leveraged", "unleveraged"):
        chosen = mode
        amount = cash + sum((t.estimated_gbp for t in sells), ZERO)
        reason = "Invest cash in the current trend position."
    elif mode == "dca" and not sells:
        # Exit proceeds must be recorded before any instalment is proposed.
        chosen = "unleveraged"
        due = dca_target(inflows, signal.exit_on, signal.as_of) - dca_spent
        amount = min(cash, max(ZERO, due))
        reason = "Monthly 1/12 instalment of the cash held since the SPY exit."
    if chosen and amount >= MIN_TRADE:
        quote = quotes[funds[chosen]]
        qty = (amount / quote.price_gbp).quantize(SHARE, rounding=ROUND_DOWN)
        value = (qty * quote.price_gbp).quantize(CENT)
        if qty > ZERO and value >= MIN_TRADE:
            buys.append(Trade(chosen, funds[chosen], "BUY", qty, value,
                              quote.price_gbp, quote.as_of, reason))
    trades = tuple(sells + buys)
    audit = (("as_of", signal.as_of.isoformat()),
             ("regime", signal.regime), ("guard", signal.guard), ("mode", mode),
             ("spy_to_ma200", str(signal.spy_ratio)),
             ("qqq_to_ma200", str(signal.qqq_ratio)),
             ("exit_on", signal.exit_on.isoformat() if signal.exit_on else ""),
             ("dca_spent", str(dca_spent)))
    summary = ("SPY is %.2f%% and QQQ is %.2f%% of their 200-day averages; "
               "the selected allocation is %s." %
               (float(signal.spy_ratio * 100), float(signal.qqq_ratio * 100), mode))
    return Result(VERSION, "action_needed" if trades else "ok",
                  "REBALANCE" if trades else "HOLD",
                  summary + (" Review the proposed manual trades." if trades else " Nothing to do."),
                  trades=trades, audit=audit)
