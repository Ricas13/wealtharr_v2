"""Single-user concise notices; user trades manually. Idempotent dispatch keys.

Use this in place of the old monthly command after suppressing instant
QUARTERLY dispatch in review_strategy (see INSTALL.md). SPIKE_WATCH and
THIRTY_DOWN remain immediate mid-quarter alerts from market_signals.py.
"""
from datetime import timedelta
from decimal import Decimal
from django.utils import timezone
from .models import (LedgerEntry, NotificationPreference, RebalanceExecution,
                     StrategyInstance, StrategySignal)
from .services import dispatch
from .cockpit import _price_status
from .market_signals import quarter_end_before
from .services import ledger_summary

D = Decimal


def trade_lines(signal):
    lines = []
    for a in signal.actions:
        if a.get('side') not in ('BUY', 'SELL'):
            continue
        lines.append('%s approx. £%s of %s (%s units at %s close)' % (
            a['side'].title(), a['estimated_gbp'], a['symbol'], a['quantity'], signal.as_of))
    return lines


def send_due_cockpit_notices(today=None, remind_after_days=3):
    today = today or timezone.localdate()
    count = 0
    for strategy in StrategyInstance.objects.select_related('owner', 'account').filter(
            preset__slug='nasdaq-quarterly-target', market_data_enabled=True).exclude(account__kind='PAPER'):
        pref, _ = NotificationPreference.objects.get_or_create(owner=strategy.owner)
        report = ledger_summary(strategy)
        fresh, why, marks = _price_status(strategy, report)
        signals = StrategySignal.objects.filter(strategy=strategy, kind='QUARTERLY').exclude(
            review_outcome__in=('FALSE_POSITIVE', 'NOT_APPLICABLE')).filter(
                as_of=quarter_end_before(today)).order_by('-as_of', '-pk')[:1]
        for sig in signals:
            if RebalanceExecution.objects.filter(signal=sig).exists():
                continue
            orders = trade_lines(sig)
            # Do not send older trade instructions when newer broker events
            # have changed the portfolio since the signal's valuation date.
            reconciled = fresh and not strategy.ledger.filter(occurred_on__gt=sig.as_of).exists()
            if orders and reconciled:
                subject = 'Wealtharr 9Sig — quarterly action (%s)' % sig.as_of
                body = ('Quarter-end review %s\n\n%s\n\nOpen Wealtharr → Today → Review trades. '
                        'Verify live prices, quantities and fees at Trading 212. '
                        'No orders have been placed.') % (sig.as_of, '\n'.join(orders))
                outcomes = dispatch(strategy.owner, 'quarterly-digest:%s:%s' % (strategy.pk, sig.signal_key),
                                    subject, body)
                count += sum(x == 'sent' for x in outcomes.values())
            elif orders and not reconciled:
                # An older review should not become a fresh executable notice.
                # Today shows exactly why instructions are unavailable.
                continue
            if (orders and reconciled and sig.created_at.date() <= today - timedelta(days=remind_after_days)
                    and not RebalanceExecution.objects.filter(signal=sig).exists()):
                subject = '9Sig review still waiting'
                body = ('The %s quarterly review has not been confirmed in Wealtharr. '
                        'Open Today to check freshness and either record actual fills '
                        'or mark the signal as not applicable. Do not trade from an old email.') % sig.as_of
                key = 'rebalance-reminder:%s:%s:%s' % (strategy.pk, sig.signal_key, today.isocalendar().week)
                outcomes = dispatch(strategy.owner, key, subject, body)
                count += sum(x == 'sent' for x in outcomes.values())
        if pref.monthly_reminders and strategy.monthly_target > 0 and today.day >= 3:
            contributed = sum((x.amount for x in strategy.ledger.filter(
                kind='DEPOSIT', occurred_on__year=today.year, occurred_on__month=today.month)), D('0'))
            if contributed < strategy.monthly_target:
                outstanding = strategy.monthly_target - contributed
                subject = 'Wealtharr — monthly £%s contribution' % strategy.monthly_target
                body = ('Planned this month: £%s. Recorded: £%s. '
                        'Remaining planned: £%s.\n\nAfter depositing in Trading 212, '
                        'open Wealtharr → Today → I deposited this. '
                        'No money has been moved automatically.') % (
                            strategy.monthly_target, contributed, outstanding)
                key = 'monthly:%s:%04d-%02d' % (strategy.pk, today.year, today.month)
                outcomes = dispatch(strategy.owner, key, subject, body)
                count += sum(x == 'sent' for x in outcomes.values())
    return count
