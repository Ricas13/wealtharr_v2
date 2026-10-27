"""Conservative 9Sig quarterly review (the 9Sig plug-in's engine adapter).

Price downloading lives in market_data and is re-exported here.

References: Jason Kelly's published Signal/30 Down explanations and the owner's
signal strategy simulation v1.07 workbook. Suggestions never place orders.
"""
import logging
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from .market_data import QUOTE_MAX_AGE
from .models import (
    LedgerEntry, MarketObservation, PriceMark, StrategySignal, YahooSync,
)
from .services import ledger_summary, dispatch
from .notification_routing import route_notice

LOG = logging.getLogger(__name__)
ZERO = Decimal("0")
CENTS = Decimal("0.01")
PRECISION = Decimal("0.0001")
# Price downloading is strategy-neutral; kept importable from here too.
from .market_data import (  # noqa: E402,F401
    TICKER, FetchedChart, fetch_chart, period_close, settlement_close, sync_ticker,
)

# Pure, immutable versioned rule implementation (Django-free).
from .strategies.nine_sig.v2 import (
    VERSION as NINE_SIG_RULE_VERSION, quarter_number, prior_quarter_name,
    quarter_end_before, _quarterly_closes, _drawdown_from_spy,
    signal_line_value, advice_for_holdings, first_100_up_crossing,
)


def count_skipped_equity_sells(quarterly_signals):
    """A BUY shortfall HOLD is not a skipped SELL under 30 Down.

    Older versions also encoded cash shortages as HOLD. Counting every HOLD
    as a deferred sale could force an incorrect 60/40 reset on the next sale.
    """
    return sum(
        any(a.get("side") == "HOLD" for a in row.actions) and
        ("defer this equity sell" in row.message.lower() or
         "defer this stock sell" in row.message.lower() or
         "skip this sell" in row.message.lower())
        for row in quarterly_signals
    )


def _alert_intraperiod_spike(strategy, *, now, first_buy, baseline, tqqq_latest,
                             version=NINE_SIG_RULE_VERSION):
    """Possible mid-quarter 100%-up rule, using verified daily US closes.

    Exact Kelly spike reference is last executed quarterly rebalance price,
    not necessarily the calendar-quarter closing price. Until that is known,
    this is a warning only, never a made-up automatic GBP trading order.
    """
    if (tqqq_latest is None or tqqq_latest.currency != "USD" or
            quarter_number(tqqq_latest.as_of) != quarter_number(now) or
            tqqq_latest.as_of > now):
        return
    last_quarter = quarter_end_before(now)
    # Do not apply another investor's previous-quarter execution reference to
    # a portfolio which only entered 9Sig during the current quarter.
    if first_buy.occurred_on > last_quarter:
        return
    if baseline is not None and baseline.as_of > last_quarter:
        return
    origin = settlement_close("TQQQ", last_quarter)
    if not origin or origin.currency != "USD" or origin.price <= ZERO:
        return
    candidates = list(MarketObservation.objects.filter(
        symbol="TQQQ", currency="USD", as_of__gt=last_quarter,
        as_of__lte=tqqq_latest.as_of,
    ).order_by("as_of").values_list("as_of", "price"))
    crossing = first_100_up_crossing(
        origin_on=last_quarter, origin_usd=origin.price,
        daily_usd=candidates, until=tqqq_latest.as_of)
    crossed = (MarketObservation.objects.filter(
        symbol="TQQQ", currency="USD", as_of=crossing[0]).first()
        if crossing else None)
    if not crossed or first_buy.occurred_on > crossed.as_of:
        return
    if baseline is not None and baseline.as_of >= crossed.as_of:
        return
    start_date = baseline.as_of if baseline else first_buy.occurred_on
    down_notice = strategy.signals.filter(
        kind="THIRTY_DOWN",
        as_of__gte=max(start_date, last_quarter - timedelta(days=750)),
    ).exclude(review_outcome__in=("FALSE_POSITIVE", "NOT_APPLICABLE")).order_by("-as_of").first()
    if down_notice and quarter_number(now) - quarter_number(down_notice.as_of) < 8:
        return
    if strategy.ledger.filter(occurred_on__gt=crossed.as_of).exists():
        return
    q = quarter_number(now)
    key = "spike:%04d-Q%d" % (q // 4, q % 4 + 1)
    message = (
        "TQQQ USD daily close %s on %s reached at least twice the "
        "preceding calendar-quarter closing reference %s. "
        "Possible 100%%-up event: review a reset to 60%% equity / 40%% "
        "safe assets. Jason Kelly's reference may be the actual prior "
        "quarterly rebalance execution price, which can differ from "
        "the calendar quarter close. Check that reference, your prior "
        "allocation and 30 Down status before trading. No GBP orders "
        "or quantities have been invented."
    ) % (crossed.price, crossed.as_of, origin.price)
    signal, created = StrategySignal.objects.get_or_create(
        strategy=strategy, signal_key=key,
        defaults={"kind": "SPIKE_WATCH",
                  "title": "9Sig 100%-up spike: manual reset review",
                  "message": message, "as_of": crossed.as_of, "actions": [],
                  "strategy_version": version})
    if created:
        route_notice(strategy.owner, "urgent",
                     "signal:%s:%s" % (strategy.pk, key),
                     "Wealtharr — " + signal.title,
                     strategy.name + "\n" + message + "\nNo orders placed.",
                     sender=dispatch, strategy=strategy)


THREE_SIG = "three-sig"


def _rules_for(strategy):
    """The pinned rule module: 9Sig v2 for pound accounts, v3 for dollar/euro ones; 3Sig v1."""
    from .strategies.nine_sig import v2, v3
    if strategy.preset.slug == THREE_SIG:
        from .strategies.three_sig import v1 as three
        ok = (strategy.strategy_version == three.VERSION
              and strategy.account.currency in three.CURRENCIES)
        return three if ok else None
    version = strategy.strategy_version or NINE_SIG_RULE_VERSION
    rules = {v2.VERSION: v2, v3.VERSION: v3}.get(version)
    if rules is v2 and strategy.account.currency != "GBP":
        return None
    if rules is v3 and strategy.account.currency not in v3.CURRENCIES:
        return None
    return rules


def review_strategy(strategy):
    """Use actual *completed-quarter* prices and ledger holdings, not today's value."""
    if not strategy.market_data_enabled or not strategy.preset.active:
        return {"status": "disabled"}
    if strategy.preset.slug not in ("nasdaq-quarterly-target", THREE_SIG):
        return {"status": "unsupported"}
    # Never run v2 against an old pinned v1 owner portfolio implicitly.
    rules = _rules_for(strategy)
    if rules is None:
        return {"status": "cannot_compute", "reason": "Pinned rule version differs; owner review required"}
    # 9Sig also watches TQQQ (its 100%-up reset); 3Sig holds no 3x fund and doesn't.
    nine_sig = strategy.preset.slug != THREE_SIG
    currency = strategy.account.currency
    if (not TICKER.fullmatch(strategy.equity_symbol) or
            not TICKER.fullmatch(strategy.safe_symbol)):
        return {"status": "invalid symbol"}
    # The live adapter must enforce the exact pinned UK trading lines just
    # as the pure evaluator, cockpit and single-fill path already do.
    from .instrument_mapping import for_strategy
    if for_strategy(strategy) is None:
        return {"status": "cannot_compute", "reason": "Unverified instrument mapping or account trading line"}
    report = ledger_summary(strategy)
    if not report["positions"]:
        return {"status": "waiting for first purchase"}
    if any(symbol not in (strategy.equity_symbol, strategy.safe_symbol)
           for symbol, qty in report["positions"].items() if qty > ZERO):
        return {"status": "holdings outside the two configured strategy sleeves"}
    tickers = {strategy.equity_symbol, strategy.safe_symbol, "SPY"} | ({"TQQQ"} if nine_sig else set())
    data = {symbol: sync_ticker(symbol) for symbol in tickers}
    # Fail closed even if old accepted observations remain in the database.
    # The health issue must be reviewed rather than silently ignored.
    if YahooSync.objects.filter(symbol__in=tickers,
            last_error__startswith="PRICE_HOLD:").exists():
        return {"status": "cannot_compute", "reason": "Market price on hold for review"}
    now = timezone.localdate()
    # Current price marks are for the portfolio dashboard, never for a past
    # quarter's signal. A failed Yahoo lookup cannot invent a new valuation.
    for symbol in (strategy.equity_symbol, strategy.safe_symbol):
        close = data.get(symbol)
        if close and close.currency == currency and close.as_of >= now - QUOTE_MAX_AGE:
            previous = PriceMark.objects.filter(strategy=strategy, symbol=symbol).first()
            if not previous or previous.as_of <= close.as_of:
                PriceMark.objects.update_or_create(
                    strategy=strategy, symbol=symbol,
                    defaults={"price": close.price, "as_of": close.as_of, "source": "yahoo"})
    # Replay the corrected (effective) ledger so owner edits and deletions
    # are reflected instead of permanently blocking reviews.
    from .v2_ledger_core import effective_entries
    entries = list(effective_entries(strategy))
    first_buy = next((e for e in entries if e.kind == LedgerEntry.BUY
                      and e.symbol == strategy.equity_symbol), None)
    if not first_buy:
        return {"status": "waiting for equity purchase"}
    quarter_end = quarter_end_before(now)
    baseline = strategy.baselines.order_by("-as_of", "-pk").first()
    start_date = baseline.as_of if baseline else first_buy.occurred_on
    if quarter_number(quarter_end) < quarter_number(start_date):
        return {"status": "waiting for first completed quarter"}
    # A reset (9Sig 60/40, 3Sig 80/20) is a suggestion, never proof that the
    # owner traded. Future cycles are paused until positions are re-anchored.
    # Only a real reset proposal (with a reason and trades) pauses them: a
    # provisional 100%-up warning has nothing to finish, so pausing on it
    # stopped reviews for good. It stays on record as an urgent warning.
    pending_reset = strategy.signals.filter(
        kind__in=("QUARTERLY", "SPIKE_WATCH"), as_of__lt=quarter_end,
        message__icontains="review a reset to ", reset_reason__gt="",
    ).exclude(review_outcome__in=("FALSE_POSITIVE", "NOT_APPLICABLE")).order_by("-as_of").first()
    if pending_reset and (baseline is None or baseline.as_of <= pending_reset.as_of):
        return {"status": "manual reset must be confirmed as a new strategy baseline"}
    if nine_sig:
        _alert_intraperiod_spike(
            strategy, now=now, first_buy=first_buy, baseline=baseline,
            tqqq_latest=data.get("TQQQ"), version=rules.VERSION)
    closing = {symbol: period_close(symbol, quarter_end) for symbol in tickers}
    if (any(x is None for x in closing.values()) or
            closing[strategy.equity_symbol].currency != currency or
            closing[strategy.safe_symbol].currency != currency or
            (nine_sig and closing["TQQQ"].currency != "USD") or closing["SPY"].currency != "USD"):
        return {"status": "incomplete or currency-mismatched quarter-end prices"}
    # Replay only actual fills dated by the quarterly settlement.
    cash = ZERO
    positions = {}
    injected = []
    for entry in (e for e in entries if e.occurred_on <= quarter_end):
        qty = positions.get(entry.symbol, ZERO)
        if entry.kind == LedgerEntry.DEPOSIT:
            cash += entry.amount
            if entry.occurred_on > start_date:
                injected.append((entry.occurred_on, entry.amount))
        elif entry.kind == LedgerEntry.WITHDRAW:
            cash -= entry.amount
            if entry.occurred_on > start_date:
                injected.append((entry.occurred_on, -entry.amount))
        elif entry.kind in (LedgerEntry.DIVIDEND, LedgerEntry.INTEREST):
            cash += entry.amount
        elif entry.kind == LedgerEntry.FEE:
            cash -= entry.amount
        elif entry.kind == LedgerEntry.BUY:
            cash -= (entry.quantity * entry.unit_price).quantize(CENTS) + entry.fee
            positions[entry.symbol] = qty + entry.quantity
        elif entry.kind == LedgerEntry.SELL:
            cash += (entry.quantity * entry.unit_price).quantize(CENTS) - entry.fee
            positions[entry.symbol] = qty - entry.quantity
        elif entry.kind == LedgerEntry.SPLIT:
            from .services import split_quantity
            positions[entry.symbol] = split_quantity(qty, entry.quantity)
    stock_qty = positions.get(strategy.equity_symbol, ZERO)
    safe_qty = positions.get(strategy.safe_symbol, ZERO)
    if stock_qty <= ZERO or safe_qty < ZERO or cash < ZERO:
        return {"status": "historical holdings cannot support an advisory review"}
    # Full observed TQQQ history is informational for the running-high
    # drawdown; the trade rules still use only completed-quarter references.
    ref_tqqq = list(MarketObservation.objects.filter(
        symbol="TQQQ", as_of__lte=quarter_end,
    ).order_by("as_of").values_list("as_of", "price")) if nine_sig else []
    ref_spy = list(MarketObservation.objects.filter(
        symbol="SPY", as_of__lte=quarter_end,
        as_of__gte=quarter_end - timedelta(days=760),
    ).order_by("as_of").values_list("as_of", "price"))
    # Separate from USD TQQQ: use only persisted normalized GBP observations
    # of the owner's actual growth trading line for information-only drawdown.
    growth_history = list(MarketObservation.objects.filter(
        symbol=strategy.equity_symbol, currency=currency, as_of__lte=quarter_end,
    ).order_by("as_of").values_list("as_of", "price"))
    # The 30 Down clock starts with a detected 30 Down notice, not
    # with a period of no sales. Previous unrelated HOLDs do not count.
    active_down_signal = strategy.signals.filter(
        kind="THIRTY_DOWN", as_of__lt=quarter_end,
        as_of__gte=start_date,
    ).exclude(review_outcome__in=("FALSE_POSITIVE", "NOT_APPLICABLE")).order_by("-as_of", "-pk").first()
    down_age = (quarter_number(quarter_end) - quarter_number(active_down_signal.as_of)
                if active_down_signal else 0)
    skipped = 0
    if active_down_signal and down_age <= 8:
        earlier = strategy.signals.filter(
            kind="QUARTERLY", as_of__lt=quarter_end,
            as_of__gte=active_down_signal.as_of,
        ).exclude(review_outcome__in=("FALSE_POSITIVE", "NOT_APPLICABLE")).order_by("as_of")
        skipped = count_skipped_equity_sells(earlier)
    # An eight-quarter SPY window must be complete and quarter-close-dated,
    # not inferred from two convenient samples. A missing previous peak
    # could silently turn a genuine 30 Down into a normal buy/sell signal.
    review_q = quarter_number(quarter_end)
    def settled_end(q):
        year, quarter_index = divmod(q, 4)
        month = 3 * (quarter_index + 1)
        return date(year, month, monthrange(year, month)[1])
    spy_closes = _quarterly_closes(ref_spy, quarter_end)
    if any(q not in spy_closes or
           (settled_end(q) - spy_closes[q][0]).days > 6
           for q in range(review_q - 8, review_q + 1)):
        return {"status": "cannot_compute",
                "reason": "SPY eight-quarter verified quarter-end history incomplete"}
    tqqq_closes = _quarterly_closes(ref_tqqq, quarter_end)
    if nine_sig and any(q not in tqqq_closes or
           (settled_end(q) - tqqq_closes[q][0]).days > 6
           for q in (review_q - 1, review_q)):
        return {"status": "cannot_compute",
                "reason": "TQQQ preceding/current verified quarter-end history incomplete"}
    # Initial equity baseline includes the first purchase date only.
    # Subsequent buys in the first calendar quarter must not silently
    # inflate the baseline and double-count their funding contributions.
    initial_equity_cost = baseline.equity_value if baseline else sum(
        (t.quantity * t.unit_price for t in entries
         if t.symbol == strategy.equity_symbol and t.kind == LedgerEntry.BUY
         and t.occurred_on == first_buy.occurred_on),
        ZERO)
    if initial_equity_cost <= ZERO:
        return {"status": "missing initial equity investment"}
    alerts = rules.advice_for_holdings(
        start_date=start_date, as_of=quarter_end,
        currency=currency,
        equity_symbol=strategy.equity_symbol,
        safe_symbol=strategy.safe_symbol,
        equity_qty=stock_qty, equity_price=closing[strategy.equity_symbol].price,
        safe_qty=safe_qty, safe_price=closing[strategy.safe_symbol].price,
        cash=cash, starting_equity_cost=initial_equity_cost,
        contributions=injected, reference=ref_tqqq, spy_reference=ref_spy,
        growth_history=growth_history,
        prior_skipped_sells=skipped, prior_drawdown=bool(active_down_signal),
        down_age_quarters=down_age,
    )
    # A 100%-up TQQQ close compared with a calendar-quarter proxy is
    # NOT proof of the owner's actual previous rebalance fill reference.
    # The pure typed v2 evaluator already refuses executable reset quantities
    # in this situation. Apply the same rule to persisted Django signals.
    provisional_spike = any(
        row["kind"] == "QUARTERLY" and
        row.get("reset_reason", "").startswith("The TQQQ reference doubled")
        for row in alerts
    )
    if provisional_spike:
        alerts = [
            dict(row, actions=[], reset_reason="",
                 message=row["message"] +
                 " Provisional TQQQ quarter-close proxy ONLY: its actual prior "
                 "rebalance execution anchor is not confirmed. No executable "
                 "reset quantities can be offered; review the reference first.")
            if row["kind"] == "QUARTERLY" else row
            for row in alerts
        ]
        review_q = quarter_number(quarter_end)
        crossing_key = "spike:%04d-Q%d" % (review_q // 4, review_q % 4 + 1)
        if not strategy.signals.filter(signal_key=crossing_key).exists():
            alerts.append({
                "kind": "SPIKE_WATCH",
                "key": "spike-quarter:%04d-Q%d" % (review_q // 4, review_q % 4 + 1),
                "title": "Possible TQQQ 100%-up: verify prior execution anchor",
                "message": (
                    "TQQQ more than doubled versus a calendar-quarter closing "
                    "proxy. The actual previous rebalance price has not been "
                    "verified. No GBP reset trades or quantities are available "
                    "until independently reviewed. No broker order was placed."
                ),
                "actions": [],
            })
    created = 0
    for alert in alerts:
        signal, new = StrategySignal.objects.get_or_create(
            strategy=strategy, signal_key=alert["key"],
            defaults={"kind": alert["kind"], "title": alert["title"],
                      "message": alert["message"], "actions": alert["actions"],
                      "reset_reason": alert.get("reset_reason", ""),
                      "strategy_version": rules.VERSION,
                      "as_of": quarter_end})
        if new:
            created += 1
            from .money import symbol_for
            details = "\n".join(
                "%s %s shares %s (estimated %s%s)" %
                (a["side"], a["quantity"], a["symbol"], symbol_for(currency), a["estimated_gbp"])
                for a in alert["actions"])
            # No-action quarterly reviews remain in history without
            # triggering rebalance alerts or cluttering the Overview.
            actionable = alert["kind"] in ("THIRTY_DOWN", "SPIKE_WATCH") or any(
                action.get("side") in ("BUY", "SELL") for action in alert["actions"])
            if actionable and alert["kind"] in ("THIRTY_DOWN", "SPIKE_WATCH"):
                route_notice(strategy.owner, "urgent",
                             "signal:%s:%s" % (strategy.pk, signal.signal_key),
                             "Wealtharr — " + signal.title,
                             strategy.name + "\n" + signal.message + "\n" + details +
                             "\nReview this in Wealtharr. No orders have been placed.",
                             sender=dispatch, strategy=strategy)
            elif not actionable:
                signal.acknowledged_at = timezone.now()
                signal.save(update_fields=["acknowledged_at"])
    return {"status": "ok", "created": created, "as_of": quarter_end.isoformat()}
