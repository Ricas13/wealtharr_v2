"""Nasdaq Trend + DCA v1. Pure, versioned end-of-day strategy.

No Django, broker, prices fetched or simulated fills. Reference signals are
SPY/QQQ in USD; trades use the account's pinned actual trading lines.
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


@dataclass(frozen=True)
class TrendSignal:
    as_of: date
    mode: str  # leveraged, unleveraged, cash, dca
    regime: str  # on, off
    exit_on: date | None
    months_elapsed: int
    spy_ratio: Decimal
    qqq_ratio: Decimal


def elapsed_months(start, end):
    """Completed monthly anniversaries since the bearish crossover, capped at 12."""
    if not start or end < start:
        return 0
    count = (end.year - start.year) * 12 + end.month - start.month
    if end.day < min(start.day, monthrange(end.year, end.month)[1]):
        count -= 1
    return min(12, max(0, count))


def trend_series(spy, qqq):
    """Full daily state replay, reusing 200-close rolling sums in O(n) time."""
    sp = {d: Decimal(str(p)) for d, p in spy if Decimal(str(p)) > ZERO}
    qp = {d: Decimal(str(p)) for d, p in qqq if Decimal(str(p)) > ZERO}
    days = sorted(set(sp) & set(qp))
    if len(days) < WINDOW:
        return {}
    regime, exit_on, out = None, None, {}
    sp_sum = sum((sp[d] for d in days[:WINDOW]), ZERO)
    q_sum = sum((qp[d] for d in days[:WINDOW]), ZERO)
    for i in range(WINDOW - 1, len(days)):
        if i >= WINDOW:
            sp_sum += sp[days[i]] - sp[days[i - WINDOW]]
            q_sum += qp[days[i]] - qp[days[i - WINDOW]]
        ratio_sp = sp[days[i]] * WINDOW / sp_sum
        ratio_qqq = qp[days[i]] * WINDOW / q_sum
        if ratio_sp > ENTER:
            regime = "on"
        elif ratio_sp < EXIT:
            if regime != "off":
                exit_on = days[i]
            regime = "off"
        if regime is None:
            continue
        if regime == "off":
            out[days[i]] = TrendSignal(
                days[i], "dca", regime, exit_on, elapsed_months(exit_on, days[i]),
                ratio_sp, ratio_qqq)
        else:
            mode = ("cash" if ratio_qqq > CASH_GUARD else
                    "unleveraged" if ratio_qqq > REDUCE else "leveraged")
            out[days[i]] = TrendSignal(
                days[i], mode, regime, None, 0, ratio_sp, ratio_qqq)
    return out


def trend_at(spy, qqq, as_of):
    """The most recent settled signal, without any future closes."""
    result = trend_series(
        ((d, p) for d, p in spy if d <= as_of),
        ((d, p) for d, p in qqq if d <= as_of))
    return result[max(result)] if result else None


def evaluate(*, funds, positions, cash, quotes, signal, completed_dca=0):
    """Recommend owner-confirmed fills; never place trades or fabricate holdings.

    DCA spends 1/(12 - completed instalments) of remaining cash at each
    due monthly step. It does not create external contribution transactions.
    """
    def refuse(code, reason):
        return Result(VERSION, "cannot_compute", code, reason)
    if signal is None:
        return refuse("HISTORY", "No settled 200-session SPY/QQQ signal is available.")
    if set(funds) != set(ROLES) or len(set(funds.values())) != len(ROLES):
        return refuse("ROLES", "Two distinct mapped funds are required.")
    if cash < ZERO or any(q < ZERO for q in positions.values()):
        return refuse("NEGATIVE", "Negative cash or holdings require reconciliation.")
    if any(s not in funds.values() for s, q in positions.items() if q > ZERO):
        return refuse("OUTSIDE", "A holding is outside the strategy's two funds.")
    if not 0 <= completed_dca <= 12:
        return refuse("DCA_STATE", "The DCA instalment history is invalid.")
    if any(k not in quotes or quotes[k].price_gbp <= ZERO for k in funds.values()):
        return refuse("PRICE", "Both mapped instruments need approved account-currency closes.")

    mode = signal.mode
    held = {r: positions.get(s, ZERO) for r, s in funds.items()}
    sells = []
    if mode in ("leveraged", "unleveraged"):
        sale_roles = [r for r in ROLES if r != mode]
    elif mode == "cash":
        sale_roles = list(ROLES)
    elif mode == "dca" and signal.months_elapsed == 0:
        sale_roles = list(ROLES)  # Exit first, DCA starts after an anniversary.
    elif mode == "dca":
        sale_roles = ["leveraged"]
    else:
        return refuse("MODE", "Unrecognised strategy mode.")
    for role in sale_roles:
        qty = held[role]
        if qty <= ZERO:
            continue
        quote = quotes[funds[role]]
        value = (qty * quote.price_gbp).quantize(CENT)
        sells.append(Trade(role, funds[role], "SELL", qty, value,
                           quote.price_gbp, quote.as_of, "Follow the SPY trend exit or QQQ guard."))

    budget = cash + sum((t.estimated_gbp for t in sells), ZERO)
    buys = []
    chosen = None
    if mode in ("leveraged", "unleveraged"):
        chosen = mode
        amount = budget
    elif mode == "dca" and signal.months_elapsed > completed_dca and not sells:
        # Exit proceeds must be actually recorded before any DCA purchase.
        # Never propose a premature monthly buy in the same review as an exit.
        chosen = "unleveraged"
        amount = budget / (12 - completed_dca)
    else:
        amount = ZERO
    if chosen and amount >= MIN_TRADE:
        quote = quotes[funds[chosen]]
        qty = (amount / quote.price_gbp).quantize(SHARE, rounding=ROUND_DOWN)
        value = (qty * quote.price_gbp).quantize(CENT)
        if qty > ZERO and value >= MIN_TRADE:
            buys.append(Trade(chosen, funds[chosen], "BUY", qty, value,
                              quote.price_gbp, quote.as_of,
                              "Monthly 1/12 DCA instalment." if mode == "dca"
                              else "Invest cash in the current trend position."))
    trades = tuple(sells + buys)
    audit = (("as_of", signal.as_of.isoformat()),
             ("regime", signal.regime), ("mode", mode),
             ("spy_to_ma200", str(signal.spy_ratio)),
             ("qqq_to_ma200", str(signal.qqq_ratio)),
             ("exit_on", signal.exit_on.isoformat() if signal.exit_on else ""),
             ("dca_months_elapsed", str(signal.months_elapsed)),
             ("dca_instalments_completed", str(completed_dca)))
    summary = ("SPY is %.2f%% and QQQ is %.2f%% of their 200-day averages; "
               "the selected allocation is %s." %
               (float(signal.spy_ratio * 100), float(signal.qqq_ratio * 100), mode))
    return Result(VERSION, "action_needed" if trades else "ok",
                  "REBALANCE" if trades else "HOLD",
                  summary + (" Review the proposed manual trades." if trades else " Nothing to do."),
                  trades=trades, audit=audit)
