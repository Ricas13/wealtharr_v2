"""All Weather, UK version 1: five fixed weights, rebalanced once a year.

Pure: no database, network or clock. The caller supplies holdings, cash and
verified GBP closes for the review date; this returns the trades that put
every fund back to its weight, or a reason it can't.

Not affiliated with Bridgewater Associates. A UK adaptation: US shares,
long and intermediate UK gilts, gold and broad commodities.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_DOWN

VERSION = "all_weather.v1.0.0"
ZERO = Decimal("0")
CENTS = Decimal("0.01")
SHARES = Decimal("0.0001")
# Trades smaller than this are left alone: not worth a dealing slip.
MIN_TRADE_GBP = Decimal("25")
WEIGHTS = (("stocks", Decimal("0.30")), ("long", Decimal("0.40")), ("inter", Decimal("0.15")),
           ("gold", Decimal("0.075")), ("comm", Decimal("0.075")))


@dataclass(frozen=True)
class Fund:
    role: str
    symbol: str
    weight: Decimal


@dataclass(frozen=True)
class Quote:
    symbol: str
    price_gbp: Decimal
    as_of: date


@dataclass(frozen=True)
class Trade:
    role: str
    symbol: str
    side: str
    quantity: Decimal
    estimated_gbp: Decimal
    price_gbp: Decimal
    price_date: date
    reason: str

    def as_action(self):
        return {"side": self.side, "symbol": self.symbol, "quantity": str(self.quantity),
                "estimated_gbp": str(self.estimated_gbp), "role": self.role,
                "price_gbp": str(self.price_gbp), "price_date": self.price_date.isoformat(),
                "reason": self.reason}


@dataclass(frozen=True)
class Result:
    strategy_version: str
    status: str  # ok, action_needed, cannot_compute
    reason_code: str
    reason: str
    trades: tuple = ()
    audit: tuple = ()

    def audit_row(self):
        return {"strategy_version": self.strategy_version, "status": self.status,
                "reason_code": self.reason_code, **dict(self.audit)}


def _cannot(code, reason):
    return Result(VERSION, "cannot_compute", code, reason)


def evaluate(*, funds, positions, cash, quotes, as_of):
    """Trades restoring each fund's weight at ``as_of`` (a year end).

    funds: Fund per role (weights must sum to 1). positions: {symbol: shares}.
    cash: GBP cash. quotes: {symbol: Quote} verified closes on/just before as_of.
    """
    if sum((f.weight for f in funds), ZERO) != Decimal("1"):
        return _cannot("WEIGHTS", "The strategy's weights don't add up to 100%.")
    symbols = {f.symbol for f in funds}
    if any(qty < ZERO for qty in positions.values()) or cash < ZERO:
        return _cannot("NEGATIVE", "A holding or cash is negative. Fix the ledger first.")
    if any(s not in symbols for s, qty in positions.items() if qty > ZERO):
        return _cannot("OUTSIDE", "You hold something outside this strategy's funds.")
    missing = [f.symbol for f in funds if f.symbol not in quotes or quotes[f.symbol].price_gbp <= ZERO]
    if missing:
        return _cannot("PRICE", "No verified year-end close for %s." % ", ".join(missing))
    value = {f.symbol: positions.get(f.symbol, ZERO) * quotes[f.symbol].price_gbp for f in funds}
    total = sum(value.values(), ZERO) + cash
    if total <= ZERO:
        return _cannot("EMPTY", "Nothing is invested yet.")
    audit = [("as_of", as_of.isoformat()), ("total_gbp", str(total.quantize(CENTS))),
             ("cash_gbp", str(cash.quantize(CENTS)))]
    sells, buys = [], []
    for f in funds:
        quote = quotes[f.symbol]
        target = total * f.weight
        gap = target - value[f.symbol]
        audit += [("%s_value" % f.role, str(value[f.symbol].quantize(CENTS))),
                  ("%s_target" % f.role, str(target.quantize(CENTS)))]
        if abs(gap) < MIN_TRADE_GBP:
            continue
        if gap < ZERO:
            qty = min((-gap / quote.price_gbp).quantize(SHARES, rounding=ROUND_DOWN),
                      positions.get(f.symbol, ZERO))
            if qty > ZERO:
                sells.append(Trade(f.role, f.symbol, "SELL", qty, (qty * quote.price_gbp).quantize(CENTS),
                                   quote.price_gbp, quote.as_of,
                                   "Above its %s%% weight." % (f.weight * 100).normalize()))
        else:
            buys.append((f, quote, gap))
    # Buys are paid for by the sells plus cash, never by borrowing.
    budget = cash + sum((t.estimated_gbp for t in sells), ZERO)
    wanted = sum((gap for _, _, gap in buys), ZERO)
    scale = min(Decimal("1"), budget / wanted) if wanted > ZERO else Decimal("1")
    bought = []
    for f, quote, gap in buys:
        qty = (gap * scale / quote.price_gbp).quantize(SHARES, rounding=ROUND_DOWN)
        cost = (qty * quote.price_gbp).quantize(CENTS)
        if qty > ZERO and cost >= MIN_TRADE_GBP:
            bought.append(Trade(f.role, f.symbol, "BUY", qty, cost, quote.price_gbp, quote.as_of,
                                "Below its %s%% weight." % (f.weight * 100).normalize()))
    trades = tuple(sells + bought)
    if not trades:
        return Result(VERSION, "ok", "ON_TARGET", "Every fund is close to its weight. Nothing to do.",
                      audit=tuple(audit))
    return Result(VERSION, "action_needed", "REBALANCE",
                  "Put each fund back to its weight: sell what's above, then buy what's below.",
                  trades=trades, audit=tuple(audit))
