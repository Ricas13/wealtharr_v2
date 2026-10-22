"""3Sig version 1: Jason Kelly's 3% Signal, run in the style of the app's 9Sig.

Pure: no database, network or clock. From the book ("The 3% Signal", 2015):
a small-company stock fund and a bond fund, 80/20 to start, a quarterly 3%
growth target for the stock side plus half of each quarter's new money, sell
the surplus into bonds or buy the shortfall from them, and rebalance to 80/20
when bonds pass 30% of the plan. Owner choice (22 Oct 2026): the 30 Down rule
and resets follow 9Sig's later style (skip the next two sell signals after SPY
falls 30% from its eight-quarter high; the third would-be sell or an
eight-quarter expiry asks for a reset), with 9Sig's large-buy throttle and
all-in reserve rule. No leveraged-fund spike rule: 3Sig holds no 3x fund.
Not affiliated with Jason Kelly or The Kelly Letter.
"""
from calendar import monthrange
from decimal import Decimal

from tracker.strategies.nine_sig.v2 import (  # noqa: F401 (shared, unchanged helpers)
    _drawdown_from_spy, _floor_qty, _order, _quarterly_closes, quarter_number,
)

VERSION = "three_sig.v1.0.0"
CURRENCIES = ("GBP", "USD", "EUR")
SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}
ZERO = Decimal("0")
CENTS = Decimal("0.01")
GROWTH = Decimal("1.03")
STOCK_SHARE = Decimal("0.80")
BOND_CEILING = Decimal("0.30")
RESERVE_EMPTY_NOTICE = "Your reserve is now empty. Review whether to reset to 80/20."
RESET_WORDS = "review a reset to 80% stock / 20% bonds and cash."


def _review_quarter(as_of):
    at_close = as_of.month % 3 == 0 and as_of.day == monthrange(as_of.year, as_of.month)[1]
    return quarter_number(as_of) if at_close else quarter_number(as_of) - 1


def signal_line_value(*, start_date, as_of, starting_equity_cost, contributions=()):
    """3% growth each completed quarter, plus half of that quarter's deposits after its growth."""
    review_quarter = _review_quarter(as_of)
    first_quarter = quarter_number(start_date)
    if review_quarter < first_quarter:
        return None
    target = Decimal(starting_equity_cost) * GROWTH ** (review_quarter - first_quarter + 1)
    for day, amount in contributions:
        day_q = quarter_number(day)
        if start_date < day <= as_of and first_quarter <= day_q <= review_quarter:
            target += (Decimal(amount) / 2) * GROWTH ** (review_quarter - day_q)
    return target.quantize(CENTS)


def advice_for_holdings(*, start_date, as_of, currency, equity_symbol, safe_symbol,
                        equity_qty, equity_price, safe_qty, safe_price, cash,
                        starting_equity_cost, reference=(), spy_reference=None,
                        contributions=(), prior_skipped_sells=0, no_sell_quarters=0,
                        prior_drawdown=False, down_age_quarters=0, growth_history=()):
    """The quarterly 3Sig advice for one completed quarter; same shape as 9Sig's."""
    if (currency not in CURRENCIES or equity_qty <= ZERO or starting_equity_cost <= ZERO or
            equity_price <= ZERO or safe_price <= ZERO or safe_qty < ZERO or cash < ZERO):
        return []
    sym = SYMBOLS[currency]
    review_q = _review_quarter(as_of)
    target = signal_line_value(start_date=start_date, as_of=as_of,
                               starting_equity_cost=starting_equity_cost, contributions=contributions)
    if target is None:
        return []
    equity_value = equity_qty * equity_price
    safe_value = safe_qty * safe_price
    total = equity_value + safe_value + cash
    if total <= ZERO:
        return []
    difference = target - equity_value
    tolerance = CENTS

    spy = _quarterly_closes(spy_reference or (), as_of)
    down_trigger, decline = _drawdown_from_spy(spy, review_q)
    active_down = down_trigger or (prior_drawdown and down_age_quarters < 8)
    alerts = []
    if down_trigger and not prior_drawdown:
        alerts.append({
            "kind": "THIRTY_DOWN", "key": "30down:" + str(review_q),
            "title": "3Sig 30 Down: next two sell signals are deferred",
            "message": (
                "SPY fell %.2f%% from its rolling eight-quarter closing peak. The next two stock "
                "sell signals are skipped; buy signals continue, with the third would-be sell "
                "requiring an 80/20 reset. The window expires after eight quarters. No trade was placed."
            ) % float(decline * 100),
            "actions": [],
        })

    reset_reason = None
    note = ""
    if safe_value + cash > total * BOND_CEILING:
        reset_reason = "Bonds and cash are above 30% of the plan"
    elif prior_drawdown and down_age_quarters >= 8:
        reset_reason = "Eight-quarter 30 Down safety window expired"
        active_down = False
    elif active_down and difference < -tolerance:
        if prior_skipped_sells >= 2:
            reset_reason = "Two sell signals have already been skipped under 30 Down"
        else:
            note = "30 Down active: defer this stock sell signal; do not rebalance."
    if not note:
        note = ("Stock fund below the quarterly signal line." if difference > tolerance else
                "Stock fund above the quarterly signal line." if difference < -tolerance else
                "Stock fund matches the quarterly signal line; no rebalance needed.")
    if reset_reason:
        note = reset_reason + ": " + RESET_WORDS
        difference = total * STOCK_SHARE - equity_value

    reserve_empty = False
    actions = []
    if active_down and not reset_reason and difference < -tolerance:
        actions.append({"side": "HOLD", "symbol": equity_symbol, "quantity": "0", "estimated_gbp": "0.00"})
    elif difference < -tolerance:
        equity_sell = min(equity_qty, _floor_qty(-difference, equity_price))
        if equity_sell > ZERO:
            actions.append(_order("SELL", equity_symbol, equity_sell, equity_price))
        safe_buy = _floor_qty(cash + equity_sell * equity_price, safe_price)
        if safe_buy > ZERO:
            actions.append(_order("BUY", safe_symbol, safe_buy, safe_price))
    elif difference > tolerance:
        reserve = cash + safe_value
        if reset_reason:
            budget = min(difference, reserve)
        elif difference > reserve:
            budget = reserve
            reserve_empty = True
            note += " Rule applied: all-in reserve shortage (not an automatic reset). " + RESERVE_EMPTY_NOTICE
        elif difference < reserve * Decimal("0.90"):
            budget = difference
        else:
            budget = min(difference, reserve * Decimal("0.81"))
            note += " The large-buy throttle (9Sig style) limits this quarter's purchase."
        safe_sale_budget = min(safe_value, max(ZERO, budget - cash))
        safe_to_sell = safe_qty if reserve_empty else min(safe_qty, _floor_qty(safe_sale_budget, safe_price))
        if safe_to_sell > ZERO:
            actions.append(_order("SELL", safe_symbol, safe_to_sell, safe_price))
        available = cash + safe_to_sell * safe_price
        equity_buy = _floor_qty(min(budget, available), equity_price)
        if equity_buy > ZERO:
            actions.append(_order("BUY", equity_symbol, equity_buy, equity_price))
        if budget + tolerance < difference:
            note += " Available buying power limits the order size."

    label = "%04d-Q%d" % (review_q // 4, review_q % 4 + 1)
    alerts.append({
        "kind": "QUARTERLY", "key": "quarter:" + label,
        "reset_reason": reset_reason or "",
        "reserve_empty_review_notice": RESERVE_EMPTY_NOTICE if reserve_empty else "",
        "rule_applied": ("All-in reserve shortage" if reserve_empty else
                         "30% bond ceiling" if reset_reason and "30%" in reset_reason else "SPY-only 30 Down"),
        "title": "3Sig quarterly review — " + label,
        "message": (
            "%s Signal line %s%s = a 3%% rise each completed quarter plus half of deposits in that "
            "quarter. Prices and proposed unit quantities are estimates based on closing quotes. "
            "Review before trading; no orders have been placed."
        ) % (note, sym, target),
        "actions": actions,
    })
    return alerts
