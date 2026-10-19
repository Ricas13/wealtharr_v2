"""Single-purpose owner cockpit. Existing engine remains the source of signals.

This module never calls a broker or changes prices. All new trading activity
is submitted as *confirmed historical executions* by the owner.
"""
from datetime import timedelta
from decimal import Decimal, ROUND_DOWN
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .cockpit_forms import FillForm, DepositShortcutForm, OneScreenSetupForm, ExecutionDateForm
from .instrument_mapping import resolve_instruments, InstrumentMappingUnavailable
from .market_signals import quarter_end_before, settlement_close, signal_line_value
from .models import (Account, BrokerImportRow, LedgerEntry, MarketObservation,
                     NotificationPreference, PriceMark, RebalanceExecution,
                     StrategyBaseline, StrategyInstance, StrategyPreset,
                     StrategySignal, StrategyValueSnapshot)
from .performance import (cash_gain, cumulative_from_annualized, simple_return,
                          xirr, snapshot_twr_ready, annualized_return, max_drawdown)
from .services import ledger_summary, validate_and_record

D = Decimal
ONE = D("1")
ZERO = D('0')
CENTS = D('0.01')


def _strategy(user):
    """Do not silently merge multiple existing portfolios into a fictional one."""
    qs = StrategyInstance.objects.filter(owner=user, preset__slug='nasdaq-quarterly-target').exclude(account__kind='PAPER').select_related('preset', 'account')
    if qs.count() > 1:
        return None, 'Multiple real 9Sig portfolios exist. Select or consolidate one primary portfolio before using this single-account cockpit; existing records are untouched.'
    return qs.first(), None


def _next_end(day):
    from calendar import monthrange
    from datetime import date
    month = ((day.month - 1) // 3 + 1) * 3
    return date(day.year, month, monthrange(day.year, month)[1])


def _price_status(strategy, report):
    today = timezone.localdate()
    if report['cash'] < ZERO:
        return False, 'Cash is negative. Reconcile missing deposits, charges, or trades.', []
    held = [s for s, q in report['positions'].items() if q > ZERO]
    if any(s not in (strategy.equity_symbol, strategy.safe_symbol) for s in held):
        return False, 'An unmodelled holding exists. Reconcile the broker portfolio first.', []
    if report['missing']:
        return False, 'A held instrument has no price. Fetch verified GBP closes.', []
    if not held:
        return False, 'Add your actual purchases or import your Trading 212 history.', []
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
    if sig and any(a.get('side') in ('BUY', 'SELL') for a in sig.actions):
        if not RebalanceExecution.objects.filter(signal=sig).exists():
            return sig
    return None


def _line_comparison(strategy, signal):
    """Quarter-end values on the SAME date. No mixing a past line with spot NAV."""
    if signal.kind != 'QUARTERLY':
        return None
    first_buy = strategy.ledger.filter(kind='BUY', symbol=strategy.equity_symbol).order_by('occurred_on', 'pk').first()
    if first_buy is None:
        return None
    baseline = strategy.baselines.filter(as_of__lte=signal.as_of).order_by('-as_of', '-pk').first()
    start = baseline.as_of if baseline else first_buy.occurred_on
    initial = baseline.equity_value if baseline else sum(
        (x.quantity*x.unit_price for x in strategy.ledger.filter(
            kind='BUY', symbol=strategy.equity_symbol, occurred_on=first_buy.occurred_on)), ZERO)
    moves = [(e.occurred_on, e.amount if e.kind == 'DEPOSIT' else -e.amount)
             for e in strategy.ledger.filter(occurred_on__gt=start, occurred_on__lte=signal.as_of,
                                             kind__in=('DEPOSIT', 'WITHDRAW'))]
    line = signal_line_value(start_date=start, as_of=signal.as_of,
                             starting_equity_cost=initial, contributions=moves)
    quote = settlement_close(strategy.equity_symbol, signal.as_of)
    if line is None or line <= ZERO or quote is None or quote.currency != 'GBP':
        return None
    qty = ZERO
    for e in strategy.ledger.filter(occurred_on__lte=signal.as_of, symbol=strategy.equity_symbol,
                                    kind__in=('BUY', 'SELL')):
        qty += e.quantity if e.kind == 'BUY' else -e.quantity
    actual = qty * quote.price
    maximum = max(line, actual)
    return {'line': line.quantize(CENTS), 'actual': actual.quantize(CENTS),
            'line_pct': line / maximum * 100, 'actual_pct': actual / maximum * 100,
            'as_of': signal.as_of}


def _performance(strategy, summary):
    snaps = list(StrategyValueSnapshot.objects.filter(strategy=strategy).order_by('as_of'))
    entries = list(strategy.ledger.filter(kind__in=('DEPOSIT', 'WITHDRAW')).order_by('occurred_on', 'pk'))
    first = entries[0].occurred_on if entries else None
    held_marks = [summary['marks'].get(sym) for sym, qty in summary['positions'].items() if qty > ZERO]
    held_marks = [m for m in held_marks if m is not None]
    end = min((m.as_of for m in held_marks), default=timezone.localdate())
    # End valuation uses the EXACT date of latest verified marks (no future flows).
    # Else XIRR remains unavailable rather than mislabel today's holdings as an old close.
    valid_end = bool(held_marks) and len(held_marks) == sum(q > ZERO for q in summary['positions'].values()) and all(
        mark.source == 'yahoo' and MarketObservation.objects.filter(
            symbol=mark.symbol, as_of=mark.as_of, currency='GBP', price=mark.price).exists()
        for mark in held_marks) and (
        max(m.as_of for m in held_marks) - end).days <= 1 and not strategy.ledger.filter(occurred_on__gt=end).exists()
    irr = xirr([(e.occurred_on, -e.amount if e.kind == 'DEPOSIT' else e.amount) for e in entries],
               end, summary['value']) if first and valid_end and summary['value'] is not None else None
    # Simple cumulative ratio is shown only for precisely one initial cash movement.
    simple = simple_return(summary['value'], entries[0].amount) if (
        len(entries) == 1 and entries[0].kind == 'DEPOSIT' and valid_end) else None
    cumulative = simple if simple is not None else (
        cumulative_from_annualized(irr, first, end) if first else None)
    twr = snapshot_twr_ready(snaps, strategy.ledger)
    annual = annualized_return(twr, snaps[0].as_of, snaps[-1].as_of) if twr is not None else irr
    # Raw snapshots with external flows are NOT drawdown-safe.
    drawdown = max_drawdown([s.total_value for s in snaps]) if twr is not None else None
    return {'cumulative': cumulative*100 if cumulative is not None else None,
            'xirr': irr*100 if irr is not None else None,
            'twr': twr*100 if twr is not None else None,
            'annual': annual*100 if annual is not None else None,
            'drawdown': drawdown*100 if drawdown is not None else None,
            'chart': [{'date': s.as_of.isoformat(), 'value': str(s.total_value),
                       'contributed': str(s.net_contributed)} for s in snaps]}


@login_required
@require_http_methods(['GET'])
def today(request):
    strategy, blocked = _strategy(request.user)
    if strategy is None and not blocked:
        return redirect('cockpit_setup')
    if blocked:
        return render(request, 'today.html', {'blocking_reason': blocked, 'empty': True})
    report = ledger_summary(strategy)
    fresh, reason, marks = _price_status(strategy, report)
    signal = _last_action(strategy)
    next_review = _next_end(timezone.localdate())
    # Historical notices are NOT executable if post-quarter transactions changed balances.
    if signal and strategy.ledger.filter(occurred_on__gt=signal.as_of).exists():
        fresh = False
        reason = 'Transactions occurred since this signal. Reconcile and obtain an updated instruction before trading.'
    if fresh and not signal:
        first_buy = strategy.ledger.filter(kind='BUY', symbol=strategy.equity_symbol).order_by('occurred_on').first()
        last_quarter = quarter_end_before(timezone.localdate())
        if not strategy.market_data_enabled:
            fresh, reason = False, '9Sig monitoring is disabled. Enable it before relying on instructions.'
        elif first_buy and first_buy.occurred_on <= last_quarter:
            needed = (strategy.equity_symbol, strategy.safe_symbol, 'TQQQ', 'SPY')
            missing = [t for t in needed if settlement_close(t, last_quarter) is None]
            if missing:
                fresh, reason = False, 'Missing last-quarter history for ' + ', '.join(missing) + '.'
            elif not strategy.signals.filter(kind='QUARTERLY', as_of=last_quarter).exists():
                fresh, reason = False, 'Quarterly rules have not produced a verified review yet.'
    perf = _performance(strategy, report)
    actions = [a for a in signal.actions if a.get('side') in ('BUY', 'SELL')] if signal else []
    actionable = bool(signal and signal.kind == 'QUARTERLY' and actions and fresh)
    needs_review = bool(signal and signal.kind in ('SPIKE_WATCH', 'THIRTY_DOWN'))
    status = ('Action needed' if actionable or needs_review else
              'Cannot compute instructions' if not fresh else
              'All good — next review ' + next_review.strftime('%d %b'))
    latest = min((m.as_of for m in marks), default=None)
    return render(request, 'today.html', {
        'strategy': strategy, 'report': report, 'perf': perf, 'signal': signal,
        'actions': actions, 'actionable': actionable, 'fresh': fresh,
        'blocking_reason': reason if not fresh else '', 'status': status,
        'next_review': next_review, 'days_left': (next_review - timezone.localdate()).days,
        'price_date': latest, 'comparison': _line_comparison(strategy, signal) if signal else None,
        'target': strategy.monthly_target,
    })


@login_required
@require_http_methods(['GET', 'POST'])
def guided_rebalance(request, signal_id):
    """GET instructions; POST actual fills + conditional immutable baseline atomically."""
    strategy, blocked = _strategy(request.user)
    if blocked or not strategy:
        raise Http404
    signal = StrategySignal.objects.filter(pk=signal_id, strategy=strategy, kind='QUARTERLY').first()
    if not signal:
        raise Http404
    actions = [a for a in signal.actions if a.get('side') in ('BUY', 'SELL')]
    if not actions or len(actions) > 4 or RebalanceExecution.objects.filter(signal=signal).exists():
        messages.error(request, 'This notice has no executable orders or is already completed.')
        return redirect('cockpit_today')
    if signal.review_outcome in ('FALSE_POSITIVE', 'NOT_APPLICABLE'):
        messages.error(request, 'This signal was marked invalid and cannot be executed.')
        return redirect('cockpit_today')
    initial = []
    for action in actions:
        q = D(action['quantity'])
        quote = settlement_close(action['symbol'], signal.as_of)
        initial.append({'quantity': q, 'unit_price': quote.price if quote and quote.currency=='GBP' else None,
                        'fee': ZERO})
    forms = [FillForm(request.POST or None, prefix='fill%s' % i, initial=item)
             for i, item in enumerate(initial)]
    if request.method == 'POST':
        when_form = ExecutionDateForm({'executed_on': request.POST.get('executed_on', '')})
        ok = when_form.is_valid() and all(f.is_valid() for f in forms)
        if ok:
            try:
                with transaction.atomic():
                    locked = StrategyInstance.objects.select_for_update().get(pk=strategy.pk, owner=request.user)
                    notice = StrategySignal.objects.select_for_update().get(pk=signal.pk, strategy=locked)
                    if RebalanceExecution.objects.filter(signal=notice).exists():
                        raise ValidationError('Already recorded. Refresh the Today page.')
                    before = ledger_summary(locked)
                    fresh, why, marks = _price_status(locked, before)
                    if not fresh:
                        raise ValidationError(why)
                    if locked.ledger.filter(occurred_on__gt=notice.as_of).exists():
                        raise ValidationError('Transactions after this quarter-end make the instruction outdated.')
                    execution_day = when_form.cleaned_data['executed_on']
                    if execution_day <= notice.as_of:
                        raise ValidationError('The execution must be after this quarter-end notice.')
                    if any(mark.as_of < execution_day for mark in marks):
                        raise ValidationError('Wait for verified Yahoo closes dated on or after your actual fills.')
                    # Preserve original signal action sequence; the ledger cannot go negative.
                    for action, form in zip(actions, forms):
                        fields = form.cleaned_data
                        entry = LedgerEntry(kind=action['side'], occurred_on=execution_day,
                                            symbol=action['symbol'], amount=ZERO,
                                            quantity=fields['quantity'], unit_price=fields['unit_price'],
                                            fee=fields['fee'], note='Confirmed execution for 9Sig signal %s' % notice.pk)
                        entry.full_clean(exclude=['strategy'])
                        validate_and_record(locked, entry)
                    after = ledger_summary(locked)
                    # Validate target within 5 portfolio percentage points.
                    if after['value'] is None or after['value'] <= ZERO:
                        raise ValidationError('Cannot verify portfolio total after the recorded fills.')
                    eq_mark = after['marks'].get(locked.equity_symbol)
                    safe_mark = after['marks'].get(locked.safe_symbol)
                    if not eq_mark or not safe_mark:
                        raise ValidationError('Both mapped instruments need verified GBP quotes.')
                    actual_equity = after['positions'].get(locked.equity_symbol, ZERO)*eq_mark.price
                    is_reset = 'review a reset to 60%' in notice.message.lower()
                    if is_reset:
                        target = after['value']*D('0.60')
                    else:
                        expected_qty = before['positions'].get(locked.equity_symbol, ZERO)
                        for item in actions:
                            if item['symbol'] == locked.equity_symbol:
                                expected_qty += D(item['quantity']) * (ONE if item['side'] == 'BUY' else -ONE)
                        target = expected_qty*eq_mark.price
                    if abs(actual_equity-target)/after['value'] > D('0.05'):
                        raise ValidationError('Equity differs by over 5 percentage points from the proposed target.')
                    if after['cash'] < ZERO or any(s not in (locked.equity_symbol, locked.safe_symbol)
                                                    for s in after['positions']):
                        raise ValidationError('Negative cash or unsupported holding after fills.')
                    baseline = None
                    if is_reset:
                        as_of = min(eq_mark.as_of, safe_mark.as_of)
                        if as_of <= notice.as_of or (max(eq_mark.as_of, safe_mark.as_of)-as_of).days > 1:
                            raise ValidationError('Reset requires matching GBP closes after the notice.')
                        if locked.baselines.filter(as_of__gt=notice.as_of).exists():
                            raise ValidationError('A newer reset baseline already exists.')
                        baseline = StrategyBaseline.objects.create(
                            strategy=locked, reset_signal=notice, as_of=as_of,
                            equity_value=actual_equity.quantize(CENTS),
                            equity_quantity=after['positions'].get(locked.equity_symbol, ZERO),
                            safe_value=(after['positions'].get(locked.safe_symbol, ZERO)*safe_mark.price).quantize(CENTS),
                            cash_value=after['cash'])
                    RebalanceExecution.objects.create(signal=notice, strategy=locked,
                                                      executed_on=execution_day, baseline=baseline)
                    notice.review_outcome = 'CONFIRMED'
                    notice.acknowledged_at = timezone.now()
                    notice.save(update_fields=['review_outcome', 'acknowledged_at'])
                messages.success(request, 'Your actual fills are recorded. No broker trades were placed by Wealtharr.')
                return redirect('cockpit_today')
            except (ValidationError, IntegrityError, ValueError) as exc:
                messages.error(request, '; '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
        else:
            messages.error(request, 'Check the execution date, quantities, prices and fees.')
    return render(request, 'rebalance.html', {'strategy': strategy, 'signal': signal,
        'rows': list(zip(actions, forms)), 'today': timezone.localdate(),
        'execution_date': request.POST.get('executed_on', timezone.localdate().isoformat()),
        'reset': 'review a reset to 60%' in signal.message.lower()})


@login_required
@require_POST
def deposit_shortcut(request):
    strategy, blocked = _strategy(request.user)
    if not strategy or blocked:
        raise Http404
    form = DepositShortcutForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Use a positive cash amount and a real deposit date.')
    else:
        try:
            validate_and_record(strategy, LedgerEntry(kind='DEPOSIT',
                occurred_on=form.cleaned_data['occurred_on'], amount=form.cleaned_data['amount'],
                note='Owner confirmed Trading 212 deposit'))
            messages.success(request, 'Deposit recorded.')
        except ValidationError as e:
            messages.error(request, '; '.join(e.messages))
    return redirect('cockpit_today')


@login_required
@require_http_methods(['GET', 'POST'])
def setup(request):
    existing, blocked = _strategy(request.user)
    if existing:
        return redirect('cockpit_today')
    if blocked:
        return render(request, 'onboarding.html', {'blocked': blocked})
    form = OneScreenSetupForm(request.POST or None, initial={'start_date': timezone.localdate(),
                                                              'account_kind': 'ISA', 'position_mode': 'FRESH'})
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        try:
            profile = resolve_instruments('nasdaq-quarterly-target', 'GB', d['account_kind'], 'TRADING212')
            with transaction.atomic():
                # Uniqueness: one account/strategy name. No made-up opening trade.
                account = Account.objects.create(owner=request.user,
                    name='Trading 212 '+d['account_kind'], kind=d['account_kind'], country='GB', broker='TRADING212')
                strategy = StrategyInstance.objects.create(
                    owner=request.user, account=account,
                    preset=StrategyPreset.objects.get(slug='nasdaq-quarterly-target', active=True),
                    name='9Sig', monthly_target=D('500.00'),
                    equity_symbol=profile.equity.yahoo_ticker,
                    safe_symbol=profile.safe.yahoo_ticker,
                    market_data_enabled=True, contribution_alerts=True)
                # Existing-holdings route: record only verified actual deposits.
                # User imports full original history next, so do not create a fake position.
                if d['position_mode'] == 'FRESH':
                    validate_and_record(strategy, LedgerEntry(kind='DEPOSIT',
                        occurred_on=d['start_date'], amount=d['starting_cash'],
                        note='Owner confirmed real opening cash'))
                prefs, _ = NotificationPreference.objects.get_or_create(owner=request.user)
                prefs.email_enabled = d['destination'] == 'EMAIL'
                prefs.discord_enabled = d['destination'] == 'DISCORD'
                prefs.destination_email = d['email'] if d['destination'] == 'EMAIL' else prefs.destination_email
                prefs.monthly_reminders = True
                prefs.save()
            if d['position_mode'] == 'EXISTING':
                messages.info(request, 'Import your complete historical deposits and fills before Wealtharr calculates 9Sig.')
                return redirect('cockpit_csv')
            if d['destination'] == 'DISCORD':
                messages.info(request, 'Add your Discord webhook in Settings, then send a test.')
            return redirect('cockpit_today')
        except (InstrumentMappingUnavailable, StrategyPreset.DoesNotExist, IntegrityError, ValidationError) as exc:
            form.add_error(None, str(exc))
    return render(request, 'onboarding.html', {'form': form})


@login_required
@require_http_methods(['GET'])
def history(request):
    strategy, blocked = _strategy(request.user)
    if not strategy:
        return redirect('cockpit_today')
    result = _performance(strategy, ledger_summary(strategy))
    return render(request, 'history_cockpit.html', {
        'strategy': strategy, 'performance': result,
        'signals': strategy.signals.order_by('-as_of', '-pk')[:60],
        'snapshots': strategy.value_snapshots.order_by('-as_of')[:60]})


@login_required
@require_http_methods(['GET'])
def ledger(request):
    strategy, blocked = _strategy(request.user)
    if not strategy:
        return redirect('cockpit_today')
    return render(request, 'ledger_cockpit.html', {
        'strategy': strategy, 'entries': strategy.ledger.order_by('-occurred_on', '-pk')[:200],
        'report': ledger_summary(strategy)})

@login_required
@require_POST
def refresh_prices(request):
    strategy, blocked = _strategy(request.user)
    if not strategy:
        raise Http404
    from .market_signals import review_strategy
    try:
        result = review_strategy(strategy)
        if result.get('status') == 'ok':
            messages.success(request, 'Yahoo prices and 9Sig rules checked. Any new action appears here.')
        else:
            messages.warning(request, 'Prices or review unavailable: ' + result.get('status', 'unknown reason'))
    except Exception:
        messages.error(request, 'Yahoo review could not be completed. No instructions were estimated.')
    return redirect('cockpit_today')
