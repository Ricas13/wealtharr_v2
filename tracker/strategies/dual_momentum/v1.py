"""Dual Momentum (Global Equities Momentum) version 1: one fund at a time, chosen monthly.

Pure: no database, network or clock. The published rules (Gary Antonacci,
"Dual Momentum Investing", 2014): at each month end compare 12-month total
returns. If US shares beat cash (absolute momentum), hold whichever of US
shares or world shares outside the US did better (relative momentum);
otherwise hold aggregate bonds. 100% in the chosen fund.
Not affiliated with Gary Antonacci or Portfolio Management Consultants.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN

from tracker.strategies.fixed_weights import Quote, Result, Trade  # noqa: F401 (re-exported)

VERSION = "dual_momentum.v1.0.0"
ROLES = ("us", "intl", "bonds")
LOOKBACK_MONTHS = 12
ZERO = Decimal("0")
CENTS = Decimal("0.01")
SHARES = Decimal("0.0001")
# Not a rule: orders under £1 can't be placed at most brokers.
MIN_TRADE_GBP = Decimal("1")
# A month-end close may be a few days earlier (weekends, holidays).
MAX_GAP_DAYS = 7


@dataclass(frozen=True)
class Lookback:
    """Closes at the start and end of the 12-month window for one series."""
    start: Quote
    end: Quote

    @property
    def total_return(self):
        return self.end.price_gbp / self.start.price_gbp - 1


def choose(us, intl, cash):
    """The published decision from three 12-month returns."""
    if us > cash:
        return "us" if us >= intl else "intl"
    return "bonds"


def _pct(value):
    return "%+.1f%%" % (value * 100)


def evaluate(*, funds, positions, cash, quotes, lookbacks, as_of, window_start):
    """Trades that put everything into the fund the momentum rules choose.

    funds: {role: symbol} for us, intl, bonds. positions: {symbol: shares}.
    cash: GBP cash. quotes: {symbol: Quote} closes at as_of for every fund.
    lookbacks: {"us"|"intl"|"cash": Lookback} for window_start -> as_of.
    """
    def cannot(code, reason):
        return Result(VERSION, "cannot_compute", code, reason)

    if set(funds) != set(ROLES):
        return cannot("ROLES", "The strategy needs a US, a world ex-US and a bond fund.")
    if any(qty < ZERO for qty in positions.values()) or cash < ZERO:
        return cannot("NEGATIVE", "A holding or cash is negative. Fix the ledger first.")
    symbols = set(funds.values())
    if any(s not in symbols for s, qty in positions.items() if qty > ZERO):
        return cannot("OUTSIDE", "You hold something outside this strategy's funds.")
    for key in ("us", "intl", "cash"):
        look = lookbacks.get(key)
        if (look is None or look.start.price_gbp <= ZERO or look.end.price_gbp <= ZERO
                or not timedelta(0) <= window_start - look.start.as_of <= timedelta(days=MAX_GAP_DAYS)
                or not timedelta(0) <= as_of - look.end.as_of <= timedelta(days=MAX_GAP_DAYS)):
            return cannot("HISTORY", "No verified 12-month price history for the %s series." % {
                "us": "US shares", "intl": "world ex-US shares", "cash": "cash"}[key])
    missing = [s for s in symbols if s not in quotes or quotes[s].price_gbp <= ZERO]
    if missing:
        return cannot("PRICE", "No verified close for %s on the review date." % ", ".join(sorted(missing)))
    returns = {k: lookbacks[k].total_return for k in ("us", "intl", "cash")}
    chosen = choose(returns["us"], returns["intl"], returns["cash"])
    target = funds[chosen]
    audit = (("as_of", as_of.isoformat()), ("window_start", window_start.isoformat()),
             ("us_return", "%.6f" % returns["us"]), ("intl_return", "%.6f" % returns["intl"]),
             ("cash_return", "%.6f" % returns["cash"]), ("chosen", chosen))
    why = "12-month returns: US shares %s, world ex-US %s, cash %s. " % (
        _pct(returns["us"]), _pct(returns["intl"]), _pct(returns["cash"]))
    why += {"us": "US shares beat cash and world ex-US: hold US shares.",
            "intl": "US shares beat cash but world ex-US did better: hold world ex-US shares.",
            "bonds": "US shares didn't beat cash: hold bonds."}[chosen]
    role_of = {s: r for r, s in funds.items()}
    sells = []
    for symbol, qty in sorted(positions.items()):
        if symbol != target and qty > ZERO:
            q = quotes[symbol]
            value = (qty * q.price_gbp).quantize(CENTS)
            if value >= MIN_TRADE_GBP:
                sells.append(Trade(role_of[symbol], symbol, "SELL", qty, value, q.price_gbp, q.as_of,
                                   "Switch out: the rules now choose another fund."))
    budget = cash + sum((t.estimated_gbp for t in sells), ZERO)
    q = quotes[target]
    qty = (budget / q.price_gbp).quantize(SHARES, rounding=ROUND_DOWN)
    cost = (qty * q.price_gbp).quantize(CENTS)
    buys = []
    if qty > ZERO and cost >= MIN_TRADE_GBP:
        buys.append(Trade(chosen, target, "BUY", qty, cost, q.price_gbp, q.as_of,
                          "The rules choose this fund; put all cash into it."))
    trades = tuple(sells + buys)
    if not trades:
        return Result(VERSION, "ok", "HOLD", why + " Nothing to do.", audit=audit)
    return Result(VERSION, "action_needed", "SWITCH" if sells else "INVEST", why, trades=trades, audit=audit)
