"""Shared checks used by Today, reviews and notifications.

Whether current prices are fresh enough to act on, the open notice for a
strategy, and the next quarter end. No broker calls and no price changes.
"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from .instrument_mapping import for_strategy
from .models import MarketObservation, RebalanceExecution, YahooSync
from .market_data import QUOTE_MAX_AGE, verified_observations

ZERO = Decimal("0")


def changed_since_review(strategy, notice):
    """Whether the ledger changed in a way that alters a quarterly review.

    New money, dividends or interest after quarter-end don't change the
    quarter's trades; trades, withdrawals or corrections made since do.
    """
    return (strategy.ledger.filter(occurred_on__gt=notice.as_of,
                                   kind__in=("BUY", "SELL", "WITHDRAW")).exists()
            or strategy.ledger_revisions.filter(created_at__gt=notice.created_at)
            .exclude(operation="ADD").exists())


def _next_end(day):
    from .core.calendar import quarter_end_on_or_after
    return quarter_end_on_or_after(day)


def _price_status(strategy, report):
    from .strategies.instruments import symbols
    from .strategies.registry import module_for
    today = timezone.localdate()
    if not strategy.market_data_enabled:
        return False, 'Price monitoring must be enabled before trading.', []
    if for_strategy(strategy) is None:
        return False, 'Your broker/account instruments are not mapped to an exact verified GBP trading line.', []
    plugin = module_for(strategy)
    funds = symbols(strategy)
    references = plugin.reference_symbols if plugin else ()
    required_symbols = funds + tuple(references(strategy) if callable(references) else references)
    on_hold = sorted(YahooSync.objects.filter(symbol__in=required_symbols, last_error__startswith='PRICE_HOLD:')
                     .values_list('symbol', flat=True))
    if on_hold:
        return False, ('%s price is on hold for checking, so no rules output is available. '
                       'The site owner can review it on the Admin page.' % ', '.join(on_hold)), []
    if any(q < ZERO for q in report['positions'].values()):
        return False, 'A holding is negative. Reconcile the ledger before trading.', []
    if report['cash'] < ZERO:
        return False, 'Cash is negative. Reconcile missing deposits, charges, or trades.', []
    held = [s for s, q in report['positions'].items() if q > ZERO]
    if any(s not in funds for s in held):
        return False, 'An unmodelled holding exists. Reconcile the broker portfolio first.', []
    if report['missing']:
        return False, 'A held instrument has no price. Fetch verified closing prices.', []
    if not held and not (plugin and plugin.allows_cash_only and report['cash'] > ZERO):
        return False, 'Record your purchases in the Ledger.', []
    # Every fund needs a verified quote, even one held at zero shares,
    # because a BUY instruction may require its price.
    marks = [report['marks'].get(s) for s in funds]
    if any(m is None for m in marks):
        return False, "Every one of the strategy's funds needs a verified close.", marks
    if any(m.source != 'yahoo' or m.price <= ZERO or m.as_of > today or
           m.as_of < today - QUOTE_MAX_AGE for m in marks):
        return False, 'Prices are stale or not Yahoo-verified. Refresh market data.', marks
    if (max(m.as_of for m in marks) - min(m.as_of for m in marks)).days > 1:
        return False, "The funds' prices have mismatched closing dates.", marks
    for m in marks:
        if not verified_observations(symbol=m.symbol, as_of=m.as_of,
                                     currency=strategy.account.currency, price=m.price).exists():
            return False, 'Price currency or source cannot be verified against market observations.', marks
    if strategy.ledger.filter(occurred_on__gt=min(m.as_of for m in marks)).exists():
        return False, 'Your newest transaction is later than the verified closing prices.', marks
    return True, '', marks


def _last_action(strategy):
    # Only the most recently completed quarter can supply executable orders.
    # Old unacknowledged notices remain in History, not resurrected as trades.
    from .strategies.registry import current_review_range
    quarter, latest = current_review_range(strategy, timezone.localdate())
    manual = strategy.signals.filter(kind__in=('SPIKE_WATCH', 'THIRTY_DOWN'),
             as_of__gte=quarter, acknowledged_at__isnull=True).exclude(
             review_outcome__in=('FALSE_POSITIVE', 'NOT_APPLICABLE')).order_by('-as_of', '-pk').first()
    if manual:
        return manual
    sig = strategy.signals.filter(kind='QUARTERLY', as_of__range=(quarter, latest)).exclude(
             review_outcome__in=('FALSE_POSITIVE', 'NOT_APPLICABLE')).order_by('-as_of', '-pk').first()
    if sig and (any(a.get('side') in ('BUY', 'SELL') for a in sig.actions)
                or 'Your reserve is now empty' in sig.message):
        if not RebalanceExecution.objects.filter(signal=sig).exists():
            return sig
    return None
