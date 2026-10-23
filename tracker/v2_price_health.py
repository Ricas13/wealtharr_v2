"""Read-only, owner-facing market-data readiness.

Use the *same* fail-closed guard as live review generation. A last successful
download or a single recent price is never evidence that the whole strategy is
ready to act on. No fetching, persistence, or broker connection.
"""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import MarketObservation, YahooSync
from .services import ledger_summary
from .signal_status import _price_status
from .strategies.instruments import symbols as fund_symbols
from .strategies.registry import module_for


def strategy_price_health(strategy, report=None):
    """Return a UI summary grounded in every required trading-line close.

    Any price-related 'ready' UI must defer to _price_status, which checks
    holds (including reference tickers), holdings, dates, Yahoo marks,
    currency-matched market observations, and transactions after the quote.
    """
    empty = {
        "ready": False, "fresh": False, "date": None, "checked": None,
        "status": "blocked", "label": "Prices not ready", "reason": "",
        "details": (), "action": "none",
    }
    if strategy.tracking_only:
        return dict(empty, status="inactive", label="Tracking only",
                    reason="Go live before using prices for instructions.",
                    action="setup")
    if report is None:
        try:
            report = ledger_summary(strategy)
        except (ValidationError, ValueError, ArithmeticError):
            return dict(empty, status="ledger", label="Ledger needs checking",
                        reason="The recorded holdings or cash could not be reconciled.",
                        action="ledger")
    ready, reason, _ = _price_status(strategy, report)
    funds = tuple(dict.fromkeys(fund_symbols(strategy)))
    plugin = module_for(strategy)
    reference = plugin.reference_symbols if plugin else ()
    references = tuple(reference(strategy) if callable(reference) else reference)
    required = set(funds) | set(references)
    syncs = {row.symbol: row for row in YahooSync.objects.filter(symbol__in=required)}
    latest_check = max(
        (row.last_attempt for row in syncs.values() if row.last_attempt is not None),
        default=None,
    )
    today = timezone.localdate()
    observation_keys = set(MarketObservation.objects.filter(
        symbol__in=funds, currency=strategy.account.currency,
        as_of__gte=today - timedelta(days=8), as_of__lte=today,
    ).values_list("symbol", "as_of", "price"))
    details = []
    missing = stale = unverified = held = False
    close_dates = []
    for symbol in funds:
        mark = report.get("marks", {}).get(symbol)
        synced = syncs.get(symbol)
        issue = ""
        if synced and synced.last_error.startswith("PRICE_HOLD:"):
            issue = "Price held for manual review"
            held = True
        elif mark is None:
            issue = "No accepted closing price"
            missing = True
        elif (mark.source != "yahoo" or mark.price <= 0 or
              (symbol, mark.as_of, mark.price) not in observation_keys):
            issue = "Price source, value or currency cannot be verified"
            unverified = True
        elif mark.as_of > today or mark.as_of < today - timedelta(days=8):
            issue = "Accepted close is outside the permitted 8-day window"
            stale = True
        if mark is not None:
            close_dates.append(mark.as_of)
        details.append({
            "symbol": symbol, "as_of": mark.as_of if mark else None,
            "issue": issue, "checked": synced.last_attempt if synced else None,
        })
    for symbol in sorted(set(references) - set(funds)):
        synced = syncs.get(symbol)
        if synced and synced.last_error.startswith("PRICE_HOLD:"):
            held = True
            details.append({"symbol": symbol, "as_of": None,
                            "issue": "Reference price held for manual review",
                            "checked": synced.last_attempt})
    if ready:
        status, label, action = "ready", "Prices verified", "none"
    elif held:
        status, label, action = "held", "Prices on hold", "admin"
    elif not strategy.market_data_enabled:
        status, label, action = "inactive", "Monitoring off", "setup"
    elif missing:
        status, label, action = "missing", "Prices missing", "wait"
    elif stale:
        status, label, action = "stale", "Prices stale", "wait"
    elif unverified:
        status, label, action = "unverified", "Prices unverified", "admin"
    elif len(close_dates) == len(funds) and close_dates and (
            max(close_dates) - min(close_dates)).days > 1:
        status, label, action = "mismatch", "Dates don't match", "wait"
    else:
        status, label, action = "blocked", "Cannot use prices", "ledger"
    # This is the *oldest* required instrument close, not the newest close.
    # Show dates for diagnosis even when readiness is withheld.
    quote_date = min(close_dates) if len(close_dates) == len(funds) and funds else None
    return {
        "ready": ready, "fresh": ready, "date": quote_date,
        "checked": latest_check, "status": status, "label": label,
        "reason": reason or "", "details": tuple(details), "action": action,
    }
