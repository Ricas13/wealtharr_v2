"""Read-only new-deposit guidance. Does not create a quarterly review or execute trades.

Only actual, effective ledger entries count. Recorded purchases reduce the
unallocated deposit pool, including partial fills; current cash is an independent
ceiling. No recommendation from held, stale or missing quotes.
"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from .market_data import quote_is_fresh, verified_observations
from .models import MarketObservation, PriceMark, YahooSync
from .strategies.contributions import allocation, FIXED_STRATEGIES
from .strategies.instruments import role_symbols
from .strategies.registry import describe_strategy, module_for
from .v2_ledger_core import effective_entries

ZERO = Decimal("0")


def uninvested_deposits(entries):
    """Conservative cash funded by actual deposits, net of *all* recorded buys.

    FIFO purchase debits reduce the outstanding deposit pool even if another
    cash source could have funded them. Sales/dividends never masquerade as
    a new contribution. Partial fills keep only the unswept remainder.
    """
    outstanding = ZERO
    last_deposit = None
    for e in entries:
        if e.kind == "DEPOSIT":
            outstanding += e.amount
            last_deposit = e.pk
        elif e.kind == "BUY":
            outstanding = max(ZERO, outstanding - e.quantity * e.unit_price - e.fee)
        elif e.kind in ("WITHDRAW", "FEE"):
            outstanding = max(ZERO, outstanding - e.amount)
    return outstanding, last_deposit


def new_money_guidance(strategy, report, *, prices_ready=False, today=None):
    """Return a suggestion with buys, or None; never infer broker fills."""
    today = today or timezone.localdate()
    # A saved trade or urgent manual review always takes priority over deposits.
    # Today, notifications and retries all call this one read-only guard.
    from .signal_status import _last_action
    if _last_action(strategy) is not None:
        return None
    if (not prices_ready or strategy.tracking_only or not strategy.market_data_enabled or
            not report["positions"] or report["cash"] <= ZERO or
            strategy.account.kind == "PAPER"):
        return None
    key = strategy.strategy_key or strategy.preset.slug
    if key == "nasdaq-trend-dca":
        # Its daily evaluator already handles fresh cash and off-regime DCA.
        return None
    desc = describe_strategy(key)
    if not desc or not desc.accepts(strategy.strategy_version):
        return None
    # A matching ticker string alone is not a verified fund or live mandate.
    from .instrument_mapping import for_strategy
    if for_strategy(strategy) is None:
        return None
    roles = role_symbols(strategy)
    if not roles or len(set(roles.values())) != len(roles):
        return None
    if any(symbol not in roles.values() for symbol, qty in report["positions"].items()
           if qty > ZERO):
        return None

    entries = [e for e in effective_entries(strategy) if e.occurred_on <= today]
    deposit_cash, last_deposit = uninvested_deposits(entries)
    if last_deposit is None:
        return None
    available = min(report["cash"], deposit_cash)
    if available < Decimal("1"):
        return None

    prices, price_dates = {}, {}
    for role, symbol in roles.items():
        if YahooSync.objects.filter(symbol=symbol, last_error__startswith="PRICE_HOLD:").exists():
            return None
        quote = PriceMark.objects.filter(strategy=strategy, symbol=symbol).first()
        if (not quote or quote.source != "yahoo" or quote.price <= ZERO or
                not quote_is_fresh(quote.as_of, today) or
                not verified_observations(
                    symbol=symbol, as_of=quote.as_of,
                    price=quote.price, currency=strategy.account.currency).exists()):
            return None  # Never rely on the caller's prices_ready alone.
        prices[role] = quote.price
        price_dates[role] = quote.as_of
    values = {r: report["positions"].get(symbol, ZERO) * prices[r]
              for r, symbol in roles.items()}
    chosen = None
    if key == "dual-momentum":
        plugin = module_for(strategy)
        weights = plugin.current_weights(strategy) if plugin and plugin.current_weights else None
        if weights is not None:
            chosen = next((r for r, symbol in roles.items()
                           if weights.get(symbol) == Decimal("1")), None)
    weights = dict(desc.role_targets) if key in FIXED_STRATEGIES else None
    chunks = allocation(key, available, values=values, weights=weights, chosen=chosen)
    if not chunks:
        return None
    suggestions = []
    for role, amount in chunks.items():
        if role not in roles or amount < Decimal("1"):
            continue
        suggestions.append({"role": role, "symbol": roles[role],
                            "amount": amount,
                            # The verified close behind the allocation; the
                            # broker's dealing price may differ.
                            "price_date": price_dates[role], "price_source": "yahoo"})
    if not suggestions:
        return None
    return {
        "amount": sum((s["amount"] for s in suggestions), ZERO),
        "rows": tuple(suggestions),
        "deposit_id": last_deposit,
        "note": ("New deposits only; buy with your broker, then record real fills. "
                 "This is not the scheduled rebalance and no trades were placed."),
    }
