"""Single-purpose owner cockpit. Existing engine remains the source of signals.

This module never calls a broker or changes prices. All new trading activity
is submitted as *confirmed historical executions* by the owner.
"""
from datetime import timedelta
import csv
from decimal import Decimal
from uuid import uuid4
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .cockpit_forms import SingleSignalFillForm, DepositShortcutForm, OneScreenSetupForm
from .instrument_mapping import enrolment_manifest, InstrumentMappingUnavailable, for_strategy
from .market_signals import quarter_end_before, settlement_close, signal_line_value
from .models import (Account, BrokerImportRow, LedgerEntry, MarketObservation,
                     NotificationPreference, PriceMark, RebalanceExecution,
                     SignalFill, StrategyBaseline, StrategyInstance, StrategyPreset,
                     StrategySignal, StrategyValueSnapshot, YahooSync)
from .performance import snapshot_twr_ready, annualized_return, max_drawdown
from .core.metrics import ExternalFlow, VerifiedValuation, evaluate_inception
from .core.isa_allowance import estimate_allowance, tax_year_bounds
from .strategies.registry import describe_strategy
from .services import ledger_summary, validate_and_record
from .core.presentation import (
    NextStep, account_value, allocation_comparison, holding_rows, ledger_display_rows, select_next_step,
    signal_display_rows, visible_performance,
)

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
    if sig and (any(a.get('side') in ('BUY', 'SELL') for a in sig.actions)
                or 'Your reserve is now empty' in sig.message):
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
    """Dated, owner-account-scoped money-weighted results plus honest TWR holds.

    No claimed cumulative return across irregular funding based on an annual
    XIRR proxy. Snapshot-only TWR is retained for periods with NO external
    flows; full boundary-unitisation lives in core.metrics and requires
    separately verified, sequenced pre/post valuations.
    """
    snaps = list(StrategyValueSnapshot.objects.filter(strategy=strategy).order_by('as_of'))
    entries = list(strategy.ledger.filter(kind__in=('DEPOSIT', 'WITHDRAW'))
                   .order_by('occurred_on', 'pk'))
    held_marks = [summary['marks'].get(sym) for sym, qty in
                  summary['positions'].items() if qty > ZERO]
    held_count = sum(q > ZERO for q in summary['positions'].values())
    end = min((m.as_of for m in held_marks if m is not None),
              default=timezone.localdate())
    observed = []
    for mark in held_marks:
        if mark is None or mark.source != 'yahoo' or mark.price <= ZERO:
            break
        proof = MarketObservation.objects.filter(
            symbol=mark.symbol, as_of=mark.as_of, currency='GBP',
            price=mark.price).values_list('pk', flat=True).first()
        if proof is None:
            break
        observed.append('market-observation:%s' % proof)
    # A money-weighted liquidation value must be verified for exactly one
    # close date; a later ledger movement cannot be valued at yesterday's NAV.
    valid_end = (bool(held_count) and len(observed) == held_count and
                 len(held_marks) == held_count and
                 all(m is not None and m.as_of == end for m in held_marks) and
                 not strategy.ledger.filter(occurred_on__gt=end).exists() and
                 summary['value'] is not None and summary['value'] >= ZERO)
    flow_rows = tuple(ExternalFlow(
        event_id='ledger:%s' % entry.pk,
        occurred_on=entry.occurred_on,
        amount_gbp=entry.amount if entry.kind == 'DEPOSIT' else -entry.amount,
        sequence=entry.pk)
        for entry in entries)
    inception = (evaluate_inception(flow_rows, VerifiedValuation(
        as_of=end, value_gbp=summary['value'],
        observation_ids=tuple(observed), verified=True))
        if valid_end else None)
    irr = inception.xirr_annual if inception and inception.status == 'ok' else None
    simple = (inception.simple_cumulative_return
              if inception and inception.status == 'ok' else None)
    twr = snapshot_twr_ready(snaps, strategy.ledger)
    annual = annualized_return(twr, snaps[0].as_of, snaps[-1].as_of) if twr is not None else irr
    # Raw snapshot drawdown is meaningful only if the window has no flows.
    drawdown = max_drawdown([s.total_value for s in snaps]) if twr is not None else None
    return {'cumulative': simple*100 if simple is not None else None,
            'xirr': irr*100 if irr is not None else None,
            'twr': twr*100 if twr is not None else None,
            'annual': annual*100 if annual is not None else None,
            'drawdown': drawdown*100 if drawdown is not None else None,
            'gain_gbp': inception.cash_gain_gbp if inception and inception.status == 'ok' else None,
            'net_contributed_gbp': inception.net_contributed_gbp if inception and inception.status == 'ok' else None,
            'metric_reason': (inception.reason_code if inception else
                              'UNVERIFIED_END_OR_INCOMPLETE_PRICE_DATES'),
            'annual_basis': ('verified_snapshot_twr' if twr is not None
                             else 'money_weighted_xirr' if irr is not None
                             else 'unavailable'),
            'xirr_as_of': end.isoformat() if irr is not None else None,
            'twr_window': ((snaps[0].as_of.isoformat(),
                            snaps[-1].as_of.isoformat()) if twr is not None
                           else None),
            'twr_reason': ('' if twr is not None else
                           'NO_PROVEN_FLOW_BOUNDARIES_OR_SNAPSHOT_PERIOD'),
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
    pending_fills = bool(signal and SignalFill.objects.filter(
        strategy=strategy, signal=signal).exists())
    next_review = _next_end(timezone.localdate())
    # Historical notices are NOT executable if post-quarter transactions changed balances.
    if signal and not pending_fills and strategy.ledger.filter(occurred_on__gt=signal.as_of).exists():
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
    # A partial manual record is bookkeeping, NOT permission to act on the old
    # recommendation or ignore stale/held quotes.
    actionable = bool(signal and signal.kind == 'QUARTERLY' and actions and
                      fresh and not pending_fills)
    needs_review = bool(signal and (
        signal.kind in ('SPIKE_WATCH', 'THIRTY_DOWN') or
        (signal.kind == 'QUARTERLY' and
         'Your reserve is now empty' in signal.message and not actions)))
    status = ('Action needed' if actionable or needs_review else
              'Cannot compute instructions' if not fresh else
              'All good — next review ' + next_review.strftime('%d %b'))
    latest = min((m.as_of for m in marks if m is not None), default=None)
    step = select_next_step(
        signal_kind=signal.kind if signal else '',
        signal_title=signal.title if signal else '',
        signal_message=signal.message if signal else '',
        proposed_actions=tuple(actions), price_ready=fresh,
        unavailable_reason=reason if not fresh else '',
        next_review_label=next_review.strftime('%d %b %Y'))
    if pending_fills and signal.kind == 'QUARTERLY':
        step = NextStep(
            "record", "Continue your recorded partial fills",
            "Some actual broker transactions are already in your ledger. "
            "Record only what you have actually executed, or finish after "
            "verified post-fill closes are available. This is not a new "
            "instruction to trade.")
    held = bool(report['positions'])
    profile = for_strategy(strategy)
    description = describe_strategy(strategy.preset.slug)
    roles = ({profile.equity.yahoo_ticker: 'Growth',
              profile.safe.yahoo_ticker: 'Defensive'} if profile else {})
    display_value = account_value(
        net_cash=report['cash'], portfolio_value=report['value'],
        prices_verified=fresh, has_holdings=held)
    return render(request, 'today.html', {
        'strategy': strategy, 'report': report, 'perf': perf, 'signal': signal,
        'actions': actions, 'actionable': actionable, 'fresh': fresh,
        'blocking_reason': reason if not fresh else '', 'status': status,
        'next_review': next_review, 'days_left': (next_review - timezone.localdate()).days,
        'price_date': latest,
        'comparison': _line_comparison(strategy, signal) if signal else None,
        'target': strategy.monthly_target,
        'next_step': step, 'has_partial_fills': pending_fills,
        'display_value': display_value,
        'display_gain': perf['gain_gbp'] if fresh else None,
        'holdings': holding_rows(report, roles=roles, show_quotes=fresh),
        'description': description,
        'allocation': allocation_comparison(
            report,
            instrument_roles=(
                {profile.equity.yahoo_ticker: 'growth',
                 profile.safe.yahoo_ticker: 'safe'} if profile else {}),
            fixed_targets=(
                dict(description.role_targets) if description else {}),
            verified=fresh),
    })


@login_required
@require_http_methods(['GET', 'POST'])
def guided_rebalance(request, signal_id):
    """Record ONE actual owner fill per POST, then verify completion separately.

    The removed legacy all-at-once handler could create a reset from submitted
    hypothetical fills without source-verified post-execution closes. It is not
    available as a second write path.
    """
    strategy, blocked = _strategy(request.user)
    if blocked or not strategy:
        raise Http404
    notice = StrategySignal.objects.filter(
        pk=signal_id, strategy=strategy, kind='QUARTERLY').first()
    if not notice:
        raise Http404
    from .cockpit_execution import (
        record_single_fill, finalize_manual_review, completion_rows,
        proposed_actions,
    )
    actions = proposed_actions(notice)
    can_choose_manual_reset = bool(
        notice.strategy_version == describe_strategy(strategy.preset.slug).version
        and not notice.reset_reason
        and 'Your reserve is now empty' in notice.message
        and not SignalFill.objects.filter(signal=notice).exists())
    if ((not actions and not can_choose_manual_reset) or len(actions) > 4 or
            RebalanceExecution.objects.filter(signal=notice).exists() or
            notice.review_outcome in ('FALSE_POSITIVE', 'NOT_APPLICABLE') or
            notice.as_of != quarter_end_before(timezone.localdate())):
        messages.error(request, 'This quarterly review is completed, invalid, or historical.')
        return redirect('cockpit_today')

    posted_form = None
    intent = request.POST.get('intent') if request.method == 'POST' else None
    if request.method == 'POST':
        if intent == 'request_manual_reset':
            if not can_choose_manual_reset:
                messages.error(request, 'This review is not eligible for a new manual reset.')
                return redirect('cockpit_today')
            try:
                from .cockpit_manual_reset import choose_manual_reserve_reset
                chosen = choose_manual_reserve_reset(
                    owner=request.user, strategy_id=strategy.pk,
                    signal_id=notice.pk)
            except (ValidationError, IntegrityError) as exc:
                messages.error(request, '; '.join(exc.messages)
                               if isinstance(exc, ValidationError) else str(exc))
            else:
                messages.success(
                    request, 'Manual 60/40 review created. No broker trade or reset was executed.')
                return redirect('cockpit_rebalance', signal_id=chosen.pk)
        elif intent == 'record_one':
            posted_form = SingleSignalFillForm(request.POST)
            if not posted_form.is_valid():
                messages.error(request, 'Enter an actual execution date, quantity, GBP unit price and fee.')
            else:
                try:
                    data = posted_form.cleaned_data
                    status, fill = record_single_fill(
                        owner=request.user, strategy_id=strategy.pk,
                        signal_id=notice.pk, action_index=data['action_index'],
                        executed_on=data['executed_on'], quantity=data['quantity'],
                        unit_price=data['unit_price'], fee=data['fee'],
                        idempotency_key=data['idempotency_key'])
                except (ValidationError, IntegrityError, ValueError) as exc:
                    messages.error(request, '; '.join(exc.messages)
                                   if isinstance(exc, ValidationError) else str(exc))
                else:
                    messages.success(request,
                        'That same fill was already saved; no duplicate was added.'
                        if status == 'duplicate' else
                        'Actual fill saved in the ledger. Review remains partial until final verification.')
                    return redirect('cockpit_rebalance', signal_id=notice.pk)
        elif intent == 'finalize':
            try:
                finalize_manual_review(owner=request.user, strategy_id=strategy.pk,
                                       signal_id=notice.pk)
            except (ValidationError, IntegrityError, ValueError) as exc:
                messages.error(request, '; '.join(exc.messages)
                               if isinstance(exc, ValidationError) else str(exc))
            else:
                messages.success(request,
                    'Recorded actual fills verified and review completed. No broker order was placed.')
                return redirect('cockpit_today')
        else:
            messages.error(request,
                'The old all-at-once fill form is retired. Record each actual fill separately.')
            return redirect('cockpit_rebalance', signal_id=notice.pk)

    existing = list(SignalFill.objects.filter(
        signal=notice, strategy=strategy).select_related('ledger_entry'))
    try:
        progress = completion_rows(notice, existing)
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
        progress = ()
    selected = None
    if posted_form:
        try:
            selected = int(request.POST.get('action_index', '-1'))
        except (TypeError, ValueError):
            pass
    rows = []
    for item in progress:
        form = (posted_form if posted_form and selected == item['index']
                else SingleSignalFillForm(initial={
                    'action_index': item['index'],
                    'fee': ZERO,
                    'idempotency_key': uuid4(),
                }))
        for field_name in ('executed_on', 'quantity', 'unit_price', 'fee'):
            form.fields[field_name].widget.attrs['id'] = (
                'actual-fill-%s-%s' % (item['index'], field_name))
        rows.append((item, form))
    return render(request, 'rebalance.html', {
        'strategy': strategy, 'signal': notice,
        'single_rows': rows,
        'has_partial_fills': bool(existing),
        'ready_to_finalize': bool(progress) and all(p['complete'] for p in progress),
        'reset': bool(notice.reset_reason),
        'can_choose_manual_reset': can_choose_manual_reset,
    })


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
            # Advisory only: count actual recorded deposits inside the relevant
            # UK tax year. We cannot see subscriptions to other ISAs, transfers
            # or whether this broker account supports flexible replacements.
            date_added = form.cleaned_data['occurred_on']
            start, end = tax_year_bounds(date_added)
            rows = strategy.ledger.filter(
                kind='DEPOSIT', occurred_on__range=(start, end)
            ).values_list('occurred_on', 'amount')
            try:
                estimate = estimate_allowance(
                    as_of=date_added, recorded_deposits=tuple(rows),
                    other_isa_subscriptions=form.cleaned_data.get(
                        'other_isa_subscriptions'))
            except ValueError:
                messages.info(request,
                    'The ISA allowance policy for this deposit year is not verified. Check HMRC.')
            else:
                if estimate.status in ('approaching', 'limit_reached'):
                    messages.warning(request, estimate.warning)
        except ValidationError as e:
            messages.error(request, '; '.join(e.messages))
    # The owner may record an actual deposit from Today or directly from Ledger.
    # Only these two fixed internal routes are supported (never an arbitrary URL).
    return redirect('cockpit_ledger' if request.POST.get('return_to') == 'ledger'
                    else 'cockpit_today')


@login_required
@require_http_methods(['GET', 'POST'])
def setup(request):
    """One-screen owner enrolment with no fictional opening events.

    Both roles and source/evidence qualifiers are pinned at creation. New
    enrolments support UK / Trading 212 / ISA only; legacy records survive.
    """
    existing, blocked = _strategy(request.user)
    if existing:
        return redirect('cockpit_today')
    if blocked:
        return render(request, 'onboarding.html', {'blocked': blocked})
    form = OneScreenSetupForm(request.POST or None)
    try:
        manifest = enrolment_manifest()
    except InstrumentMappingUnavailable:
        return render(request, 'onboarding.html', {
            'blocked': 'The reviewed UK Trading 212 Stocks ISA instrument mapping is currently unavailable.'})
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        try:
            with transaction.atomic():
                # No user-selected brokerage, currency, ticker, account type or
                # strategy rules. The owner alone chooses real funding dates.
                account, created = Account.objects.select_for_update().get_or_create(
                    owner=request.user, name='Trading 212 ISA',
                    defaults={'kind': 'ISA', 'country': 'GB',
                              'broker': 'TRADING212', 'currency': 'GBP'})
                if not created and (
                        account.kind != 'ISA' or account.country != 'GB' or
                        account.broker != 'TRADING212' or
                        account.currency != 'GBP' or
                        account.strategies.exists()):
                    raise ValidationError(
                        'An account with this name already exists with another '
                        'profile or investments. Resolve it before starting 9Sig.')
                strategy = StrategyInstance.objects.create(
                    owner=request.user, account=account,
                    preset=StrategyPreset.objects.get(
                        slug=manifest['strategy_key'], active=True),
                    name='9Sig',
                    monthly_target=d['monthly_target'],
                    equity_symbol=manifest['roles']['growth']['yahoo_ticker'],
                    safe_symbol=manifest['roles']['safe']['yahoo_ticker'],
                    strategy_version=describe_strategy(manifest['strategy_key']).version,
                    mapping_version=manifest['mapping_version'],
                    resolved_instruments=manifest,
                    market_data_enabled=True, contribution_alerts=True)
                actual_cash = d.get('starting_cash')
                if d['position_mode'] == 'FRESH' and actual_cash is not None:
                    validate_and_record(strategy, LedgerEntry(
                        kind='DEPOSIT', occurred_on=d['start_date'],
                        amount=actual_cash,
                        note='Owner confirmed actual opening GBP cash'))
                prefs, _ = NotificationPreference.objects.get_or_create(owner=request.user)
                prefs.email_enabled = d['destination'] == 'EMAIL'
                prefs.discord_enabled = d['destination'] == 'DISCORD'
                if d['destination'] == 'EMAIL':
                    prefs.destination_email = d['email']
                prefs.monthly_reminders = True
                prefs.save()
            if d['position_mode'] == 'EXISTING':
                messages.info(request,
                    'Account created with no invented holdings. Preview your complete Trading 212 history next; nothing is imported until you confirm.')
                return redirect('cockpit_csv')
            if d['destination'] == 'DISCORD':
                messages.info(request,
                    'Set your Discord destination in Settings and send a test before relying on alerts.')
            if d['position_mode'] == 'FRESH' and actual_cash is not None:
                try:
                    advisory = estimate_allowance(
                        as_of=d['start_date'],
                        recorded_deposits=((d['start_date'], actual_cash),))
                except ValueError:
                    messages.info(request,
                        'The ISA subscription limit for this deposit year has not been verified; check HMRC.')
                else:
                    if advisory.status in ('approaching', 'limit_reached'):
                        messages.warning(request, advisory.warning)
            if d.get('starting_cash') is None:
                messages.info(request,
                    'No deposit was recorded. Record money only once it has actually reached your ISA.')
            return redirect('cockpit_today')
        except (InstrumentMappingUnavailable, StrategyPreset.DoesNotExist,
                IntegrityError, ValidationError) as exc:
            form.add_error(None, str(exc))
    return render(request, 'onboarding.html', {
        'form': form, 'manifest': manifest})


@login_required
@require_http_methods(['GET'])
def history(request):
    strategy, blocked = _strategy(request.user)
    if not strategy:
        return redirect('cockpit_today')
    result = _performance(strategy, ledger_summary(strategy))
    records = list(strategy.signals.order_by('-as_of', '-pk')[:60])
    snapshots = list(strategy.value_snapshots.order_by('-as_of')[:100])
    executed = dict(RebalanceExecution.objects.filter(
        strategy=strategy, signal_id__in=[signal.pk for signal in records]
    ).values_list('signal_id', 'executed_on'))
    partial_counts = {}
    for signal_id in SignalFill.objects.filter(
            strategy=strategy, signal_id__in=[signal.pk for signal in records]
            ).values_list('signal_id', flat=True):
        partial_counts[signal_id] = partial_counts.get(signal_id, 0) + 1
    return render(request, 'history_cockpit.html', {
        'strategy': strategy, 'performance': result,
        'metrics': visible_performance(result),
        'signals': signal_display_rows(
            records, executions=executed, partial_counts=partial_counts),
        'snapshots': snapshots,
    })



@login_required
@require_http_methods(['GET'])
def ledger(request):
    """Filtered owner-only event ledger with safe, source-faithful CSV export."""
    strategy, blocked = _strategy(request.user)
    if not strategy:
        return redirect('cockpit_today')
    kind = request.GET.get('kind', '')
    ordering = request.GET.get('order', 'newest')
    output = request.GET.get('format', 'html')
    valid_kinds = {k for k, _ in LedgerEntry.TYPES}
    if kind not in valid_kinds:
        kind = ''
    if ordering not in ('newest', 'oldest'):
        ordering = 'newest'
    qs = strategy.ledger.all()
    if kind:
        qs = qs.filter(kind=kind)
    qs = qs.order_by('occurred_on', 'pk') if ordering == 'oldest' else qs.order_by('-occurred_on', '-pk')
    if output == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="wealtharr-ledger.csv"'
        writer = csv.writer(response)
        writer.writerow(('entry_id', 'date', 'kind', 'symbol', 'quantity',
                         'unit_price_gbp', 'amount_gbp', 'fee_gbp', 'note',
                         'recorded_at'))
        for row in qs.iterator(chunk_size=500):
            # Prevent spreadsheet formula injection in user-controlled text;
            # Decimal values remain numeric without unsafe coercion.
            def safe_text(value):
                value = str(value or '')
                return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value
            writer.writerow((row.pk, row.occurred_on.isoformat(), row.kind,
                             safe_text(row.symbol), str(row.quantity), str(row.unit_price),
                             str(row.amount), str(row.fee), safe_text(row.note),
                             row.created_at.isoformat()))
        return response
    entries = list(qs[:200])
    return render(request, 'ledger_cockpit.html', {
        'strategy': strategy, 'entries': ledger_display_rows(entries),
        'report': ledger_summary(strategy),
        'entry_limit_reached': qs.count() > 200,
        'filter_kind': kind, 'sort_order': ordering,
        'kind_choices': LedgerEntry.TYPES,
    })


@login_required
@require_http_methods(['GET'])
def strategy_info(request):
    """Read-only strategy explanation and pinned instrument identities."""
    strategy, blocked = _strategy(request.user)
    if not strategy:
        return redirect('cockpit_today')
    profile = for_strategy(strategy)
    instruments = tuple(
        {'role': title, 'instrument': instrument}
        for title, instrument in (
            ('Growth', profile.equity), ('Defensive', profile.safe))
    ) if profile else ()
    return render(request, 'strategy_info.html', {
        'strategy': strategy,
        'profile': profile,
        'description': describe_strategy(strategy.preset.slug),
        'instruments': instruments,
        'mapping_status': ('Issuer / catalogue evidence reviewed; personal ISA access not confirmed'
                           if profile else
                           'No reviewed instrument profile for this account'),
    })


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
