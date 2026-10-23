"""Read-only per-instrument price diagnostics for strategy UI.

This describes stored quote evidence, NOT permission to trade. The rules,
ledger and current review still decide whether a recommendation is available.
No HTTP request or price write occurs here.
"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from .models import MarketObservation, PriceMark, YahooSync
from .strategies.instruments import symbols as fund_symbols
from .strategies.registry import module_for


def price_health(strategy):
    """Inspect all required funds, including any held reference price."""
    funds = tuple(dict.fromkeys(fund_symbols(strategy)))
    off = {"state": "off", "label": "Price monitoring off", "date": None,
           "checked": None, "rows": (),
           "summary": "Enable the strategy to receive market-data checks and advisory reviews.",
           "has_hold": False}
    if strategy.tracking_only or not strategy.market_data_enabled:
        return off
    if not funds:
        return {**off, "state": "attention", "label": "No mapped funds",
                "summary": "This strategy has no pinned instruments to check."}

    plugin = module_for(strategy)
    references = plugin.reference_symbols if plugin else ()
    references = references(strategy) if callable(references) else references
    related = tuple(dict.fromkeys((*funds, *(references or ()))))
    syncs = {item.symbol: item for item in YahooSync.objects.filter(symbol__in=related)}
    checked = max((item.last_attempt for item in syncs.values()
                   if item.last_attempt), default=None)
    marks = {item.symbol: item for item in PriceMark.objects.filter(
        strategy=strategy, symbol__in=funds)}
    marked_dates = {item.as_of for item in marks.values()}
    observations = {(item.symbol, item.as_of, item.price) for item in
                    MarketObservation.objects.filter(
                        symbol__in=funds, currency=strategy.account.currency,
                        as_of__in=marked_dates)}
    today = timezone.localdate()
    cutoff = today - timedelta(days=8)
    rows, current_dates = [], []
    for symbol in funds:
        mark, sync = marks.get(symbol), syncs.get(symbol)
        held = bool(sync and sync.last_error.startswith("PRICE_HOLD:"))
        as_of = mark.as_of if mark else None
        if held:
            state, detail = "hold", "Price on hold for verification; new advice must wait."
        elif mark is None:
            state, detail = "missing", "No accepted close; await the automatic price check."
        elif as_of < cutoff or as_of > today:
            state, detail = "stale", "Accepted close is outside the eight-day freshness window."
        elif mark.source != "yahoo" or mark.price <= Decimal("0"):
            state, detail = "unverified", "Saved close is not a positive Yahoo-sourced price."
        elif (symbol, as_of, mark.price) not in observations:
            state, detail = "unverified", "Close does not match a dated quote in the account currency."
        else:
            state, detail = "ok", "Accepted close matches a dated observation."
            current_dates.append(as_of)
        rows.append({"symbol": symbol, "state": state, "date": as_of,
                     "detail": detail, "checked": sync.last_attempt if sync else None})
    reference_holds = [symbol for symbol in related if symbol not in funds
                       and syncs.get(symbol) and
                       syncs[symbol].last_error.startswith("PRICE_HOLD:")]
    for symbol in reference_holds:
        rows.append({"symbol": symbol, "state": "hold", "date": None,
                     "detail": "Reference market data is held for checking.",
                     "checked": syncs[symbol].last_attempt})
    bad = [row for row in rows if row["state"] != "ok"]
    if not bad and len(current_dates) > 1 and (max(current_dates)-min(current_dates)).days > 1:
        for row in rows:
            row["state"] = "mismatch"
            row["detail"] = "Fund closing dates differ by more than one day."
        bad = rows
    if any(row["state"] == "hold" for row in bad):
        state, label = "hold", "Price held for review"
        summary = "A fund or reference price is held. Do not rely on new instructions until cleared."
    elif bad:
        state, label = "attention", "Prices need attention"
        summary = "Required fund quotes are missing, stale, misaligned or not matched to saved evidence."
    else:
        state, label = "ok", "Fund closes current"
        summary = "Fund closes are current and matched; actionable reviews still require ledger and rule checks."
    return {"state": state, "label": label, "date": min(current_dates, default=None),
            "checked": checked, "rows": tuple(rows), "summary": summary,
            "has_hold": state == "hold"}
