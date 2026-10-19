"""9Sig fixed rules v2 — deterministic, pure, no Django, network, or persistence.

The legacy advice API and its 9%/half-contribution/30 Down rule recurrence are
lifted from market_signals.py. The typed evaluation contract adds auditable
intermediates, explicit quote provenance and a non-tradable spike warning.
This version changes only owner-approved reserve depletion and informational display.
The SPY 30 Down, no sell-half, spike and timed reset rules remain unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN
from typing import Literal

VERSION = "nine_sig.v2.0.0"
ZERO = Decimal("0")
CENTS = Decimal("0.01")
PRECISION = Decimal("0.0001")


@dataclass(frozen=True)
class FixedParams:
    version: str = VERSION
    quarterly_growth: Decimal = Decimal("1.09")
    contribution_fraction: Decimal = Decimal("0.50")
    threshold_30_down: Decimal = Decimal("0.70")
    reset_equity_fraction: Decimal = Decimal("0.60")
    reserve_throttle: Decimal = Decimal("0.81")
    thirty_down_quarters: int = 8


PARAMS = FixedParams()


@dataclass(frozen=True)
class CashFlow:
    occurred_on: date
    amount: Decimal  # deposit positive; withdrawal negative


@dataclass(frozen=True)
class NormalizedQuote:
    symbol: str
    as_of: date
    price: Decimal
    currency: str
    unit: str  # normalized GBP (pounds), or USD; GBX is NEVER treated as GBP
    source: str  # verified vendor name; not an implicit endorsement of a quote
    raw_unit: str  # captured FROM feed, not inferred by strategy
    raw_unit_verified: bool
    raw_close: Decimal | None = None
    observation_id: str = ""


@dataclass(frozen=True)
class PortfolioHistory:
    start_date: date
    as_of: date  # completed calendar quarter end
    equity_symbol: str
    safe_symbol: str
    equity_qty: Decimal
    safe_qty: Decimal
    cash: Decimal
    initial_equity_value: Decimal
    contributions: tuple[CashFlow, ...] = ()
    prior_skipped_sells: int = 0
    prior_drawdown: bool = False
    down_age_quarters: int = 0
    strategy_version: str = VERSION
    mapping_version: str = "2026-10-19.1"
    outside_holdings: bool = False
    pending_unverified_reset: bool = False


@dataclass(frozen=True)
class ApprovedPriceBundle:
    equity: NormalizedQuote
    safe: NormalizedQuote
    tqqq: tuple[NormalizedQuote, ...]
    spy: tuple[NormalizedQuote, ...]


@dataclass(frozen=True)
class AuditItem:
    name: str
    value: str


@dataclass(frozen=True)
class TradeProposal:
    role: Literal["growth", "safe"]
    symbol: str
    side: Literal["BUY", "SELL"]
    estimated_gbp: Decimal
    approximate_shares: Decimal
    price_gbp: Decimal
    price_date: date


@dataclass(frozen=True)
class EvaluationResult:
    strategy_version: str
    status: Literal["ok", "action_needed", "cannot_compute"]
    reason: str
    alerts: tuple[dict, ...]
    audit: tuple[AuditItem, ...]
    reason_code: str | None = None
    trades: tuple[TradeProposal, ...] = ()
    urgent: bool = False
    event_code: str | None = None

    def audit_row(self):
        """Stable ordered values for CSV export; all amounts remain strings."""
        return {"strategy_version": self.strategy_version, "status": self.status,
                "reason_code": self.reason_code or "",
                **{item.name: item.value for item in self.audit}}


def _audit(**values):
    return tuple(AuditItem(key, str(value)) for key, value in values.items())


def quarter_number(day):
    return day.year * 4 + (day.month - 1) // 3


def prior_quarter_name(day):
    index = quarter_number(day) - 1
    return "%04d-Q%d" % (index // 4, index % 4 + 1)


def quarter_end_before(day):
    """Last completed calendar quarter; no mid-quarter valuation used for a signal."""
    if day.month <= 3:
        return date(day.year - 1, 12, 31)
    if day.month <= 6:
        return date(day.year, 3, 31)
    if day.month <= 9:
        return date(day.year, 6, 30)
    return date(day.year, 9, 30)


def _quarterly_closes(reference, until):
    closes = {}
    for day, price in sorted(reference):
        if day <= until and price > ZERO:
            closes[quarter_number(day)] = (day, price)
    return closes


def _amount(quantity, price):
    return str((quantity * price).quantize(CENTS))


def _order(side, symbol, qty, price):
    return {"side": side, "symbol": symbol, "quantity": str(qty),
            "estimated_gbp": _amount(qty, price)}


def _floor_qty(amount, price):
    if amount <= ZERO or price <= ZERO:
        return ZERO
    return (amount / price).quantize(PRECISION, rounding=ROUND_DOWN)


def signal_line_value(*, start_date, as_of, starting_equity_cost, contributions=()):
    """Reproduce the workbook AV recurrence without advancing deposits early.

    Each completed quarter grows the line 9%; half of contributions deposited
    *during* that quarter enter only after that growth, per
    AV4 = AV3 * (1 + C9) + BV4/2. Earlier deposits grow in later quarters.
    """
    from calendar import monthrange
    at_close = as_of.month % 3 == 0 and as_of.day == monthrange(as_of.year, as_of.month)[1]
    review_quarter = quarter_number(as_of) if at_close else quarter_number(as_of) - 1
    first_quarter = quarter_number(start_date)
    if review_quarter < first_quarter:
        return None
    target = Decimal(starting_equity_cost) * Decimal("1.09") ** (review_quarter - first_quarter + 1)
    for day, amount in contributions:
        day_q = quarter_number(day)
        if start_date < day <= as_of and first_quarter <= day_q <= review_quarter:
            target += (Decimal(amount) / 2) * Decimal("1.09") ** (review_quarter - day_q)
    return target.quantize(CENTS)


def signal_line_trace(*, start_date, as_of, starting_equity_cost, contributions=()):
    """Ordered per-quarter audit of 1.09 growth + half the ACTUAL dated flows.

    Tuple fields: quarter id, opening line, net in-quarter contribution,
    ending line before final penny rounding. No monthly schedule assumptions.
    """
    from calendar import monthrange
    at_close = as_of.month % 3 == 0 and as_of.day == monthrange(as_of.year, as_of.month)[1]
    review_q = quarter_number(as_of) if at_close else quarter_number(as_of) - 1
    if review_q < quarter_number(start_date):
        return ()
    value = Decimal(starting_equity_cost)
    result = []
    for q in range(quarter_number(start_date), review_q + 1):
        in_quarter = sum((Decimal(amount) for day, amount in contributions
            if start_date < day <= as_of and quarter_number(day) == q), ZERO)
        opening = value
        value = opening * Decimal("1.09") + in_quarter / Decimal("2")
        result.append((q, opening, in_quarter, value))
    return tuple(result)


def _drawdown_from_spy(spy, review_q):
    """30 Down: current SPY quarter close <=70% of past eight closes' high.

    The fund need not be falling *this* quarter: a partial recovery can still
    end 30% below the rolling peak. The original test wrongly required a
    negative quarter-over-quarter return and missed valid triggers.
    """
    current = spy.get(review_q)
    previous = [spy[q][1] for q in range(review_q - 8, review_q)
                if q in spy and spy[q][1] > ZERO]
    if not current or not previous or current[1] <= ZERO:
        return False, ZERO
    high = max(previous)
    decline = Decimal(1) - current[1] / high
    return current[1] <= high * Decimal("0.70"), decline


def _growth_reference_drawdown(reference, as_of):
    """TQQQ reference decline from running high, never a decision trigger.

    This is not a quote history for the actual UK LQQ3 trading instrument.
    """
    observations = sorted((day, price) for day, price in reference
                          if day <= as_of and price > ZERO)
    if not observations:
        return None
    high = max(price for _, price in observations)
    return ((Decimal("1") - observations[-1][1] / high) *
            Decimal("100")).quantize(CENTS)


RESERVE_EMPTY_NOTICE = "Your reserve is now empty. Review whether to reset to 60/40."
INFORMATION_ONLY_NOTICE = "Information only, doesn't affect the advice."


def advice_for_holdings(*, start_date, as_of, currency, equity_symbol, safe_symbol,
                        equity_qty, equity_price, safe_qty, safe_price, cash,
                        starting_equity_cost, reference,
                        spy_reference=None, contributions=(), prior_skipped_sells=0,
                        no_sell_quarters=0, prior_drawdown=False,
                        down_age_quarters=0):
    """Deterministic owner-review 9Sig action on a completed quarter.

    60/40 baseline, 9% per quarter, half of additional contributions, workbook
    cash-reservoir buying throttle (0.9 x 0.9 only for large buys), 30 Down
    two-sell skip, eight-quarter expiry, 100%-up TQQQ reset and a
    full-reserve buy when the shortfall exceeds available assets. No auto-trades.
    """
    if (currency != "GBP" or equity_qty <= ZERO or starting_equity_cost <= ZERO or
            equity_price <= ZERO or safe_price <= ZERO or safe_qty < ZERO or cash < ZERO):
        return []
    from calendar import monthrange
    at_close = as_of.month % 3 == 0 and as_of.day == monthrange(as_of.year, as_of.month)[1]
    review_q = quarter_number(as_of) if at_close else quarter_number(as_of) - 1
    target = signal_line_value(
        start_date=start_date, as_of=as_of,
        starting_equity_cost=starting_equity_cost, contributions=contributions)
    if target is None:
        return []

    equity_value = equity_qty * equity_price
    safe_value = safe_qty * safe_price
    total = equity_value + safe_value + cash
    if total <= ZERO:
        return []
    difference = target - equity_value
    # A 0.5% portfolio-wide deadband was invented by the app. The published
    # strategy and uploaded workbook have no such rule. Penny rounding
    # suppresses untradeably small differences instead.
    tolerance = CENTS

    spy = _quarterly_closes(spy_reference or (), as_of)
    down_trigger, decline = _drawdown_from_spy(spy, review_q)
    # Once triggered, the skip window persists until the third would-be sell
    # or eight-quarter expiry. A recovery alone does not consume skipped sells.
    active_down = down_trigger or (prior_drawdown and down_age_quarters < 8)
    alerts = []
    if down_trigger and not prior_drawdown:
        alerts.append({
            "kind": "THIRTY_DOWN",
            "key": "30down:" + str(review_q),
            "title": "9Sig 30 Down: next two sell signals are deferred",
            "message": (
                "SPY fell %.2f%% from its rolling eight-quarter closing peak. "
                "The next two equity sell signals are skipped; buy signals "
                "continue, with the third would-be sell requiring a 60/40 reset. "
                "The window expires after eight quarters. No trade was placed."
            ) % float(decline * 100),
            "actions": [],
        })

    # Preserve this immutable historical series for informational drawdown audit.
    reference = tuple(reference)
    tqqq = _quarterly_closes(reference, as_of)
    current = tqqq.get(review_q)
    previous = tqqq.get(review_q - 1)
    spike = (previous is not None and current is not None and previous[1] > ZERO
             and current[1] >= 2 * previous[1])
    reset_reason = None
    note = ""
    if prior_drawdown and down_age_quarters >= 8:
        reset_reason = "Eight-quarter 30 Down safety window expired"
        active_down = False
    elif active_down and difference < -tolerance:
        if prior_skipped_sells >= 2:
            reset_reason = "Two sell signals have already been skipped under 30 Down"
        else:
            note = "30 Down active: defer this equity sell signal; do not rebalance."
    elif spike and not active_down:
        reset_reason = "The TQQQ reference doubled (gained at least 100%) in a quarter"

    if not note:
        note = ("Equity below quarterly signal line."
                if difference > tolerance else
                "Equity above quarterly signal line."
                if difference < -tolerance else
                "Equity matches the quarterly signal line; no rebalance needed.")

    if reset_reason:
        note = reset_reason + ": review a reset to 60% equity / 40% safe and cash."
        difference = total * Decimal("0.60") - equity_value

    reserve_empty = False
    actions = []
    if active_down and not reset_reason and difference < -tolerance:
        actions.append({"side": "HOLD", "symbol": equity_symbol,
                        "quantity": "0", "estimated_gbp": "0.00"})
    elif difference < -tolerance:
        equity_sell = min(equity_qty, _floor_qty(-difference, equity_price))
        if equity_sell > ZERO:
            actions.append(_order("SELL", equity_symbol, equity_sell, equity_price))
        # All proceeds plus existing cash can fund safe purchases. The cash
        # remainder from rounding stays in the strategy account.
        safe_budget = cash + equity_sell * equity_price
        safe_buy = _floor_qty(safe_budget, safe_price)
        if safe_buy > ZERO:
            actions.append(_order("BUY", safe_symbol, safe_buy, safe_price))
    elif difference > tolerance:
        reserve = cash + safe_value
        if reset_reason:
            # A 60/40 reset targets the current total, not the old signal
            # line. The normal large-buy throttle does not apply.
            budget = min(difference, reserve)
        elif difference > reserve:
            # v2 decision 3: exhaust the available reserve even in 30 Down.
            # This is advisory, not a new confirmed 60/40 reset baseline.
            budget = reserve
            reserve_empty = True
            note += " Rule applied: all-in reserve shortage (not an automatic reset)."
            note += " " + RESERVE_EMPTY_NOTICE
        elif difference < reserve * Decimal("0.90"):
            # Existing rule: below 90% shortfall, fully fund it.
            budget = difference
        else:
            # Existing 90%-100% large-buy throttle remains 81%.
            budget = min(difference, reserve * Decimal("0.81"))
            note += " 9Sig's large-buy throttle limits this quarter's purchase."
        safe_sale_budget = min(safe_value, max(ZERO, budget - cash))
        safe_to_sell = (safe_qty if reserve_empty else
                        min(safe_qty, _floor_qty(safe_sale_budget, safe_price)))
        if safe_to_sell > ZERO:
            actions.append(_order("SELL", safe_symbol, safe_to_sell, safe_price))
        available = cash + safe_to_sell * safe_price
        equity_buy = _floor_qty(min(budget, available), equity_price)
        if equity_buy > ZERO:
            actions.append(_order("BUY", equity_symbol, equity_buy, equity_price))
        if budget + tolerance < difference:
            note += " Available GBP buying power limits the order size."

    growth_drawdown = _growth_reference_drawdown(reference, as_of)
    reference_note = (
        "Rule applied: only SPY's quarter-end close versus the preceding eight "
        "SPY closes triggers 30 Down; manual workbook TQQQ flags and mid-quarter "
        "sell-half overrides do not apply. TQQQ growth-reference drawdown from "
        "running high: %s%%. %s"
        % (growth_drawdown if growth_drawdown is not None else "unavailable",
           INFORMATION_ONLY_NOTICE))
    label = "%04d-Q%d" % (review_q // 4, review_q % 4 + 1)
    alerts.append({
        "kind": "QUARTERLY", "key": "quarter:" + label,
        "reset_reason": reset_reason or "",
        "reserve_empty_review_notice": RESERVE_EMPTY_NOTICE if reserve_empty else "",
        "growth_reference_drawdown_pct": (str(growth_drawdown) if growth_drawdown is not None else ""),
        "rule_applied": ("All-in reserve shortage" if reserve_empty else "SPY-only 30 Down"),
        "title": "9Sig quarterly review — " + label,
        "message": (
            "%s Signal line £%s = a 9%% rise each completed quarter "
            "plus half of deposits in that quarter. GBP prices and proposed "
            "unit quantities are estimates based on closing quotes. "
            "Review before trading; no orders have been placed."
        ) % (note + " " + reference_note, target),
        "actions": actions,
    })
    return alerts


def first_100_up_crossing(*, origin_on, origin_usd, daily_usd, until):
    """Pure provisional TQQQ 2x threshold; never creates GBP reset orders.

    Caller must first verify USD identity/provenance for each input. The
    checked executed-reference caveat belongs in the UI warning.
    """
    if (not isinstance(origin_usd, Decimal) or not origin_usd.is_finite()
            or origin_usd <= ZERO or until < origin_on):
        return None
    valid = sorted((day, price) for day, price in daily_usd
                   if origin_on < day <= until and isinstance(price, Decimal)
                   and price.is_finite() and price > ZERO)
    return next(((day, price) for day, price in valid
                 if price >= origin_usd * Decimal("2")), None)


def _price_ok(quote, symbol, currency, as_of, *, expected_raw):
    if (not isinstance(quote, NormalizedQuote) or not isinstance(quote.price, Decimal)
            or not isinstance(quote.raw_close, Decimal)):
        return False
    if not quote.price.is_finite() or not quote.raw_close.is_finite():
        return False
    divisor = Decimal("100") if expected_raw == "GBX" else Decimal("1")
    # Require the captured unnormalised observation and exactly one conversion.
    return (quote.symbol == symbol and quote.currency == currency and
            quote.unit == currency and quote.raw_unit == expected_raw and
            quote.raw_unit_verified is True and quote.source in ("yahoo", "stooq") and
            bool(quote.observation_id) and quote.price > ZERO and
            quote.raw_close > ZERO and quote.price == quote.raw_close / divisor and
            Decimal("0.000001") <= quote.price <= Decimal("1000000") and
            quote.as_of <= as_of)


def evaluate(history: PortfolioHistory, prices: ApprovedPriceBundle,
             params: FixedParams = PARAMS, as_of: date | None = None) -> EvaluationResult:
    """Pure advisory only; malformed or unsupported inputs yield an audit HOLD.

    The caller owns source verification, quote date normalization, and broker
    permissions. Stooq is an independent USD-reference fallback ONLY; UK
    execution sleeves must come from the GBP Yahoo vendor path at this stage.
    No broker API, side effects, risk modelling or lookahead.
    """
    def hold(reason, code="CANNOT_COMPUTE", **audit):
        return EvaluationResult(VERSION, "cannot_compute", reason, (), _audit(**audit),
                                reason_code=code)

    if history.strategy_version != VERSION or history.mapping_version != "2026-10-19.1":
        return hold("The pinned strategy or instrument mapping version is unavailable",
                    code="UNSUPPORTED_VERSION")
    # Version alone does not allow substituting a same-currency but distinct
    # ETP, GBP line or bond fund. The code catalogue pins exact trading lines.
    if (history.equity_symbol, history.safe_symbol) != ("LQQ3.L", "ERNS.L"):
        return hold("Recorded instruments are not the pinned 9Sig trading lines",
                    code="INSTRUMENT_MISMATCH")
    if (history.equity_symbol != "LQQ3.L" or history.safe_symbol != "ERNS.L"):
        return hold("Strategy version does not contain these exact trading lines",
                    code="UNSUPPORTED_MAPPING")
    if history.outside_holdings or history.pending_unverified_reset:
        return hold("Unsupported holding or unconfirmed previous reset", code="INVALID_HISTORY")
    if params != PARAMS:
        return hold("Unsupported 9Sig parameters/version; no implicit rule changes")
    day = history.as_of if as_of is None else as_of
    if day != history.as_of:
        return hold("Evaluation date disagrees with the immutable portfolio snapshot",
                    code="AS_OF_MISMATCH")
    from calendar import monthrange
    if day.month not in (3, 6, 9, 12) or day.day != monthrange(day.year, day.month)[1]:
        return hold("Evaluation date is not a completed quarter end")
    if (not isinstance(history.initial_equity_value, Decimal) or
            not history.initial_equity_value.is_finite() or
            history.start_date > day or history.initial_equity_value <= ZERO):
        return hold("Missing or invalid equity starting basis")
    values = (history.equity_qty, history.safe_qty, history.cash, history.initial_equity_value)
    if any(not isinstance(value, Decimal) or not value.is_finite()
           for value in values):
        return hold("Invalid portfolio monetary input", code="INVALID_HISTORY")
    if (history.equity_qty <= ZERO or history.safe_qty < ZERO or history.cash < ZERO):
        return hold("Invalid portfolio quantities or cash")
    if history.prior_skipped_sells not in (0, 1, 2) or history.down_age_quarters < 0:
        return hold("Invalid 30 Down state")
    for flow in history.contributions:
        if (not isinstance(flow.amount, Decimal) or not flow.amount.is_finite() or
                flow.occurred_on <= history.start_date or
                flow.occurred_on > day):
            return hold("Contribution date/amount needs reconciliation")
    if not _price_ok(prices.equity, history.equity_symbol, "GBP", day, expected_raw="GBX"):
        return hold("Growth quote requires verified GBX-to-GBP normalization")
    if not _price_ok(prices.safe, history.safe_symbol, "GBP", day, expected_raw="GBP"):
        return hold("Safe quote requires verified GBP units")
    if prices.equity.source != "yahoo" or prices.safe.source != "yahoo":
        return hold("Independent feed not approved for UK execution instrument quotes")
    # Opposite-market holiday/weekend is permissible within the preceding six days.
    if any(day - q.as_of > timedelta(days=6) for q in (prices.equity, prices.safe)):
        return hold("Missing recent completed-quarter UK closes")
    if abs((prices.equity.as_of - prices.safe.as_of).days) > 1:
        return hold("UK quarter-end closing dates do not align", code="MISMATCHED_DATES")
    tqqq = tuple(sorted(prices.tqqq, key=lambda q: q.as_of))
    spy = tuple(sorted(prices.spy, key=lambda q: q.as_of))
    for symbol, seq in (("TQQQ", tqqq), ("SPY", spy)):
        if not seq or any(not _price_ok(q, symbol, "USD", day, expected_raw="USD") for q in seq):
            return hold("Reference prices missing or USD units/source unverified", symbol=symbol)
        if len({q.as_of for q in seq}) != len(seq):
            return hold("Reference quotes contain duplicate dates", symbol=symbol)
    review_q = quarter_number(day)
    tq = _quarterly_closes(((q.as_of, q.price) for q in tqqq), day)
    sq = _quarterly_closes(((q.as_of, q.price) for q in spy), day)
    if review_q not in tq or review_q-1 not in tq:
        return hold("TQQQ two-quarter reference missing")
    # A quote from early in a quarter is not a valid *quarter-end* reference.
    # Require a proven close within six calendar days before every quarter
    # boundary. The dates remain the observed trading days, not fabricated
    # calendar quarter-end prices.
    from calendar import monthrange
    def q_end(q):
        year, index = divmod(q, 4)
        month = (index + 1) * 3
        return date(year, month, monthrange(year, month)[1])
    if any(q_end(q) - tq[q][0] > timedelta(days=6)
           for q in (review_q - 1, review_q)):
        return hold("TQQQ reference has an early-quarter rather than quarter-end close",
                    code="MISSING_HISTORY")
    # A 30 Down cannot be declared clear without its eight-quarter lookback.
    if any(q not in sq for q in range(review_q - 8, review_q + 1)):
        return hold("SPY eight-quarter reference history incomplete")
    if any(q_end(q) - sq[q][0] > timedelta(days=6)
           for q in range(review_q - 8, review_q + 1)):
        return hold("SPY history lacks verified quarter-end closes",
                    code="MISSING_HISTORY")
    from calendar import monthrange
    for seq in (tqqq, spy):
        for q in seq:
            quarter_month = (q.as_of.month - 1) // 3 * 3 + 3
            quarter_end = date(q.as_of.year, quarter_month,
                               monthrange(q.as_of.year, quarter_month)[1])
            if (quarter_end - q.as_of).days > 6:
                return hold("Reference history contains a mid-quarter price",
                            code="STALE_PRICE")
    if any(day - q.as_of > timedelta(days=6) for q in (tqqq[-1], spy[-1])):
        return hold("Reference closing prices are stale", code="STALE_PRICE")
    contributions = tuple((flow.occurred_on, flow.amount) for flow in history.contributions)
    recurrence = signal_line_trace(start_date=history.start_date, as_of=day,
        starting_equity_cost=history.initial_equity_value, contributions=contributions)
    signal = signal_line_value(start_date=history.start_date, as_of=day,
        starting_equity_cost=history.initial_equity_value, contributions=contributions)
    eq_value = history.equity_qty * prices.equity.price
    safe_value = history.safe_qty * prices.safe.price
    reserve = safe_value + history.cash
    total = reserve + eq_value
    if signal is None or total <= ZERO:
        return hold("No valid historical portfolio valuation")
    triggered, decline = _drawdown_from_spy(sq, review_q)
    doubled = tq[review_q][1] >= tq[review_q-1][1] * Decimal("2")
    gap = signal - eq_value
    reserve_shortage = gap > reserve
    throttle = (gap > CENTS and not reserve_shortage and
                gap >= reserve * Decimal("0.90"))
    buy_budget = (reserve if reserve_shortage else
                  min(gap, reserve * Decimal("0.81")) if throttle else
                  min(gap, reserve) if gap > CENTS else ZERO)
    alerts = advice_for_holdings(
        start_date=history.start_date, as_of=day, currency="GBP",
        equity_symbol=history.equity_symbol, safe_symbol=history.safe_symbol,
        equity_qty=history.equity_qty, equity_price=prices.equity.price,
        safe_qty=history.safe_qty, safe_price=prices.safe.price,
        cash=history.cash, starting_equity_cost=history.initial_equity_value,
        reference=((q.as_of, q.price) for q in tqqq),
        spy_reference=((q.as_of, q.price) for q in spy),
        contributions=contributions,
        prior_skipped_sells=history.prior_skipped_sells,
        prior_drawdown=history.prior_drawdown,
        down_age_quarters=history.down_age_quarters)
    if doubled:
        # Calendar-quarter TQQQ close is NOT necessarily the owner's prior
        # execution anchor. Never send executable quantities from this proxy.
        alerts = [dict(alert, actions=[]) if alert["kind"] == "QUARTERLY"
                  else alert for alert in alerts]
        alerts.append({"kind": "SPIKE_WATCH", "key": "spike-warning:" + str(review_q),
                       "title": "Potential 100%-up: manual reference review",
                       "message": "TQQQ doubled versus previous quarter close; actual prior rebalance fill reference is unconfirmed. No automatic reset instructions.",
                       "actions": []})
    quarterly_plan = next((a for a in alerts if a["kind"] == "QUARTERLY"), None)
    reset_reason = quarterly_plan.get("reset_reason", "") if quarterly_plan else ""
    proposed = tuple(TradeProposal(
        "growth" if action["symbol"] == history.equity_symbol else "safe",
        action["symbol"], action["side"], Decimal(action["estimated_gbp"]),
        Decimal(action["quantity"]),
        prices.equity.price if action["symbol"] == history.equity_symbol else prices.safe.price,
        prices.equity.as_of if action["symbol"] == history.equity_symbol else prices.safe.as_of)
        for alert in alerts if alert["kind"] == "QUARTERLY"
        for action in alert["actions"] if action.get("side") in ("BUY", "SELL"))
    warnings = tuple(alert["kind"] for alert in alerts if alert["kind"] != "QUARTERLY")
    return EvaluationResult(VERSION, "action_needed" if proposed else "ok", "", tuple(alerts),
        _audit(strategy_version=VERSION, review_quarter=review_q,
            source_equity=prices.equity.source, source_safe=prices.safe.source,
            source_tqqq=tqqq[-1].source, source_spy=spy[-1].source,
            normalized_currency="GBP", equity_close_date=prices.equity.as_of,
            growth_raw_unit=prices.equity.raw_unit, safe_raw_unit=prices.safe.raw_unit,
            growth_raw_close=prices.equity.raw_close, safe_raw_close=prices.safe.raw_close,
            growth_quote_id=prices.equity.observation_id,
            safe_quote_id=prices.safe.observation_id,
            quarter_recurrence=";".join("%s:%s:%s:%s" % row for row in recurrence),
            buy_budget=buy_budget, throttle_81pct=throttle,
            reset_reason=reset_reason,
            reserve_empty_review_notice=(quarterly_plan.get("reserve_empty_review_notice", "")
                                         if quarterly_plan else ""),
            rule_applied=(quarterly_plan.get("rule_applied", "") if quarterly_plan else ""),
            growth_reference_drawdown_pct=_growth_reference_drawdown(
                ((q.as_of, q.price) for q in tqqq), day),
            growth_drawdown_label="TQQQ growth reference, not the UK execution instrument",
            growth_drawdown_disclosure=INFORMATION_ONLY_NOTICE,
            proposed_growth_target=(total * PARAMS.reset_equity_fraction
                                    if reset_reason else signal),
            proposed_growth_buy_shares=sum((t.approximate_shares for t in proposed
                if t.role == "growth" and t.side == "BUY"), ZERO),
            proposed_growth_sell_shares=sum((t.approximate_shares for t in proposed
                if t.role == "growth" and t.side == "SELL"), ZERO),
            actually_actionable_gbp=sum((t.estimated_gbp for t in proposed
                if t.side == "BUY"), ZERO),
            safe_close_date=prices.safe.as_of, initial_equity=history.initial_equity_value,
            contributed_this_review=sum((f.amount for f in history.contributions
                if quarter_number(f.occurred_on)==review_q),ZERO),
            signal_line=signal, equity_value=eq_value, safe_value=safe_value,
            cash=history.cash, portfolio_value=total, reserve=reserve,
            signal_difference=signal-eq_value, spy_peak=max(sq[q][1]
                for q in range(review_q-8, review_q)),
            spy_decline_pct=(decline*100).quantize(CENTS),
            thirty_down_triggered=triggered,
            thirty_down_active=(triggered or
                (history.prior_drawdown and history.down_age_quarters < 8)),
            prior_skipped_sells=history.prior_skipped_sells,
            tqqq_previous=tq[review_q-1][1],
            tqqq_current=tq[review_q][1], potential_spike=doubled,
            provisional_spike_reference="calendar-quarter close, not confirmed execution"),
        trades=proposed, urgent=bool(warnings),
        event_code="9sig:%s:%s" % (day.isoformat(), ",".join(warnings)) if warnings else None)
