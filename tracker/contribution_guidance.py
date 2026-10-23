"""Read-only new-deposit guidance. Does not create a quarterly review or execute trades.

Only actual, effective ledger entries count. A later recorded trade ends the
cash batch (avoids double advising a partial fill); the current cash balance
is an independent ceiling. No recommendation from held/stale/missing quotes.
"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from .models import PriceMark, YahooSync
from .strategies.contributions import allocation, FIXED_STRATEGIES
from .strategies.instruments import role_symbols
from .strategies.registry import describe_strategy, module_for
from .v2_ledger_core import effective_entries

ZERO = Decimal("0")


def new_money_guidance(strategy, report, *, prices_ready=False, today=None):
    """Return a suggestion with buys, or None; never infer broker fills."""
    today = today or timezone.localdate()
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
    roles = role_symbols(strategy)
    if not roles or len(set(roles.values())) != len(roles):
        return None
    if any(symbol not in roles.values() for symbol, qty in report["positions"].items()
           if qty > ZERO):
        return None

    entries = [e for e in effective_entries(strategy) if e.occurred_on <= today]
    last_trade = max((i for i, row in enumerate(entries)
                      if row.kind in ("BUY", "SELL", "SPLIT")), default=-1)
    pending = entries[last_trade + 1:]
    deposits = [e for e in pending if e.kind == "DEPOSIT"]
    if not deposits:
        return None
    available = (sum((e.amount for e in deposits), ZERO)
                 - sum((e.amount for e in pending if e.kind in ("WITHDRAW", "FEE")), ZERO))
    available = min(report["cash"], available)
    if available < Decimal("1"):
        return None

    prices = {}
    for role, symbol in roles.items():
        if YahooSync.objects.filter(symbol=symbol, last_error__startswith="PRICE_HOLD:").exists():
            return None
        quote = PriceMark.objects.filter(strategy=strategy, symbol=symbol).first()
        if (not quote or quote.price <= ZERO or quote.as_of > today or
                today - quote.as_of > timedelta(days=8)):
            return None
        prices[role] = quote.price
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
                            "amount": amount, "price_date": None})
    if not suggestions:
        return None
    return {
        "amount": sum((s["amount"] for s in suggestions), ZERO),
        "rows": tuple(suggestions),
        "deposit_id": deposits[-1].pk,
        "last_trade_id": entries[last_trade].pk if last_trade >= 0 else 0,
        "note": ("New deposits only; buy with your broker, then record real fills. "
                 "This is not the scheduled rebalance and no trades were placed."),
    }
