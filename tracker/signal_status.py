"""Shared checks used by Today, reviews and notifications.

Whether current prices are fresh enough to act on, the open notice for a
strategy, and the next quarter end. No broker calls and no price changes.
"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from .instrument_mapping import for_strategy
from .market_signals import quarter_end_before
from .models import MarketObservation, RebalanceExecution, YahooSync

ZERO = Decimal("0")


def _next_end(day):
    from calendar import monthrange
    from datetime import date
    month = ((day.month - 1) // 3 + 1) * 3
    return date(day.year, month, monthrange(day.year, month)[1])


def _price_status(strategy, report):
    today = timezone.localdate()
    if not strategy.market_data_enabled or strategy.account.currency != 'GBP':
        return False, '9Sig monitoring must be enabled for a GBP account before trading.', []
    if for_strategy(strategy) is None:
        return False, 'Your broker/account instruments are not mapped to an exact verified GBP trading line.', []
    required_symbols = (strategy.equity_symbol, strategy.safe_symbol, 'TQQQ', 'SPY')
    if YahooSync.objects.filter(symbol__in=required_symbols,
                                last_error__startswith='PRICE_HOLD:').exists():
        return False, 'A required market price is on hold for verification. No trade instruction is available.', []
    if any(q < ZERO for q in report['positions'].values()):
        return False, 'A holding is negative. Reconcile the ledger before trading.', []
    if report['cash'] < ZERO:
        return False, 'Cash is negative. Reconcile missing deposits, charges, or trades.', []
    held = [s for s, q in report['positions'].items() if q > ZERO]
    if any(s not in (strategy.equity_symbol, strategy.safe_symbol) for s in held):
        return False, 'An unmodelled holding exists. Reconcile the broker portfolio first.', []
    if report['missing']:
        return False, 'A held instrument has no price. Fetch verified GBP closes.', []
    if not held:
        return False, 'Record your purchases in the Ledger.', []
    # 9Sig also needs a verified safe-asset quote when that sleeve holds zero
    # shares, because a BUY/SELL instruction may require its price.
    required = list(dict.fromkeys([strategy.equity_symbol, strategy.safe_symbol]))
    marks = [report['marks'].get(s) for s in required]
    if any(m is None for m in marks):
        return False, 'Both 9Sig instruments need verified GBP closes.', marks
    if any(m.source != 'yahoo' or m.price <= ZERO or m.as_of > today or
           m.as_of < today - timedelta(days=8) for m in marks):
        return False, 'Prices are stale or not Yahoo-verified. Refresh market data.', marks
    if (max(m.as_of for m in marks) - min(m.as_of for m in marks)).days > 1:
        return False, 'Equity and safe-asset prices have mismatched closing dates.', marks
    for m in marks:
        if not MarketObservation.objects.filter(symbol=m.symbol, as_of=m.as_of,
                                                currency='GBP', price=m.price).exists():
            return False, 'Price currency or source cannot be verified against market observations.', marks
    if strategy.ledger.filter(occurred_on__gt=min(m.as_of for m in marks)).exists():
        return False, 'Your newest transaction is later than the verified closing prices.', marks
    return True, '', marks


def _last_action(strategy):
    # Only the most recently completed quarter can supply executable orders.
    # Old unacknowledged notices remain in History, not resurrected as trades.
    quarter = quarter_end_before(timezone.localdate())
    manual = strategy.signals.filter(kind__in=('SPIKE_WATCH', 'THIRTY_DOWN'),
             as_of__gte=quarter, acknowledged_at__isnull=True).exclude(
             review_outcome__in=('FALSE_POSITIVE', 'NOT_APPLICABLE')).order_by('-as_of', '-pk').first()
    if manual:
        return manual
    sig = strategy.signals.filter(kind='QUARTERLY', as_of=quarter).exclude(
             review_outcome__in=('FALSE_POSITIVE', 'NOT_APPLICABLE')).order_by('-pk').first()
    if sig and (any(a.get('side') in ('BUY', 'SELL') for a in sig.actions)
                or 'Your reserve is now empty' in sig.message):
        if not RebalanceExecution.objects.filter(signal=sig).exists():
            return sig
    return None
