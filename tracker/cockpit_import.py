"""Conservative Trading 212 CSV import. Preview first; nothing written if any row fails.

The Trading 212 export format varies by year/account. Never infer FX or ISIN
mapping; unknown rows must be reconciled, not silently skipped.
"""
import csv
import hashlib
import io
import re
from datetime import datetime, timezone as datetime_timezone
from decimal import Decimal, InvalidOperation
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .models import BrokerImportRow, LedgerEntry
from .services import ledger_summary, validate_and_record
from .cockpit import _strategy
from .instrument_mapping import for_strategy, for_historical_import

D = Decimal
ZERO = D('0')
CHARGE_STEMS = ('charge amount', 'currency conversion fee', 'transaction fee',
                'stamp duty reserve tax', 'finra fee', 'french transaction tax')


def _number(value, name):
    raw = str(value or '').strip().replace(',', '')
    try:
        result = D(raw)
    except InvalidOperation as e:
        raise ValueError(f'{name}: missing or invalid number') from e
    if not result.is_finite():
        raise ValueError(f'{name}: invalid amount')
    return result


def _event_instant(value):
    raw = (value or '').strip()
    try:
        parsed = datetime.fromisoformat(raw.replace(' ', 'T').replace('Z', '+00:00'))
    except ValueError as e:
        raise ValueError('Unrecognised time/date column') from e
    # Trading 212 exports without timezone information are interpreted as UTC.
    # Do not compare naive with offset-aware timestamps (would raise TypeError).
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime_timezone.utc)
    return parsed.astimezone(datetime_timezone.utc)


def _when(value):
    return _event_instant(value).date()


def _total_gbp(row):
    if row.get('Total (GBP)') not in (None, ''):
        return abs(_number(row['Total (GBP)'], 'Total (GBP)'))
    if row.get('Currency (Total)') == 'GBP' and row.get('Total'):
        return abs(_number(row['Total'], 'Total'))
    raise ValueError('GBP total missing; non-GBP cash events need broker reconciliation')


def _fees(row):
    sum_fee = ZERO
    for key, raw in row.items():
        if raw in (None, ''):
            continue
        head = key.lower().strip()
        if head.startswith(CHARGE_STEMS):
            # Always decline unsupported currency of fee; exchange rate alone
            # does not prove the precise broker fee allocation.
            if ('(GBP)' not in key and not (key == 'Charge amount' and
                   row.get('Currency (Charge amount)') == 'GBP')):
                raise ValueError(f'Fee currency not confirmed as GBP: {key}')
            sum_fee += abs(_number(raw, key))
    return sum_fee.quantize(D('0.01'))


def _canonical(row, strategy):
    action = (row.get('Action') or '').strip().lower()
    when = _when(row.get('Time') or row.get('Date'))
    if when > timezone.localdate():
        raise ValueError('Future transaction date')
    if action == 'deposit':
        kind, amount, symbol, qty, price, fee = 'DEPOSIT', _total_gbp(row), '', ZERO, ZERO, ZERO
    elif action == 'withdrawal':
        kind, amount, symbol, qty, price, fee = 'WITHDRAW', _total_gbp(row), '', ZERO, ZERO, ZERO
    elif action.startswith('dividend'):
        kind, amount, symbol, qty, price, fee = 'DIVIDEND', _total_gbp(row), '', ZERO, ZERO, ZERO
        raw_ticker = (row.get('Ticker') or '').strip().upper()
        if raw_ticker and raw_ticker not in (strategy.equity_symbol, strategy.safe_symbol,
                                              strategy.equity_symbol.removesuffix('.L'),
                                              strategy.safe_symbol.removesuffix('.L')):
            raise ValueError('Dividend of an unmapped holding')
    elif action in ('fee', 'custody fee', 'account fee'):
        kind, amount, symbol, qty, price, fee = 'FEE', _total_gbp(row), '', ZERO, ZERO, ZERO
    elif ('buy' in action or 'sell' in action) and any(x in action for x in ('market', 'limit', 'stop')):
        kind = 'BUY' if 'buy' in action else 'SELL'
        broker_symbol = (row.get('Ticker') or '').strip().upper()
        profile = for_historical_import(strategy)
        if profile is None:
            raise ValueError('No verified broker-to-Yahoo instrument mapping; review in Settings')
        mapped = {}
        for instrument in profile.instruments:
            for alias in (instrument.broker_ticker, instrument.broker_ticker + '.GB',
                          instrument.yahoo_ticker):
                mapped[alias] = instrument
        if broker_symbol not in mapped:
            raise ValueError(f'Unknown ticker {broker_symbol}; do not substitute securities')
        instrument = mapped[broker_symbol]
        if not (row.get('ISIN') or '').strip():
            raise ValueError('Trade has no ISIN; exact instrument identity must be verified')
        if row['ISIN'].strip().upper() != instrument.isin:
            raise ValueError('Broker ISIN does not match mapped instrument: ' + broker_symbol)
        symbol = instrument.yahoo_ticker
        qty = abs(_number(row.get('No. of shares'), 'No. of shares'))
        price = _number(row.get('Price / share'), 'Price / share')
        price_ccy = (row.get('Currency (Price / share)') or '').upper()
        if price_ccy in ('GBX', 'GBPENCE', 'GBP PENCE'):
            price = price / 100
        elif price_ccy != 'GBP':
            raise ValueError('Foreign currency trade price; FX reconciliation required')
        fee = _fees(row)
        amount = ZERO
        if qty <= ZERO or price <= ZERO:
            raise ValueError('Shares and GBP execution price must be positive')
        # Guard against a malformed price/currency header. Trading212 totals
        # may include fees; reconcile to a one-penny margin plus disclosed fees.
        if row.get('Total (GBP)'):
            gross = (qty * price).quantize(D('0.01'))
            total = _total_gbp(row)
            if min(abs(gross-total), abs(gross+fee-total), abs(gross-fee-total)) > D('0.03'):
                raise ValueError('GBP total does not match shares × GBP price and fees')
    else:
        raise ValueError(f'Unsupported Action: {row.get("Action", "(missing)")}')
    if amount < ZERO or fee < ZERO or (kind in ('DEPOSIT', 'WITHDRAW', 'DIVIDEND', 'FEE') and amount <= ZERO):
        raise ValueError('Nonpositive cash movement')
    raw_id = (row.get('ID') or '').strip()
    if len(raw_id) > 200:
        raise ValueError('Broker event ID too long to reconcile safely')
    identity = (raw_id, row.get('Time') or row.get('Date'), kind, symbol,
                str(qty), str(price), str(amount), str(fee))
    digest = hashlib.sha256(repr(identity).encode('utf-8')).hexdigest()
    return {'fingerprint': digest, 'broker_id': raw_id, 'kind': kind, 'occurred_on': when,
            'event_time': _event_instant(row.get('Time') or row.get('Date')),
            'symbol': symbol,
            'quantity': qty, 'unit_price': price, 'amount': amount, 'fee': fee,
            'note': f'Trading 212 CSV ID {raw_id}'[:280],
            'summary': f'{when} {kind} {symbol} {qty if symbol else amount} GBP'}


def inspect_csv(upload, strategy):
    if upload.size > 4_000_000:
        raise ValueError('File over 4 MB; split export into smaller periods')
    try:
        text = upload.read().decode('utf-8-sig')
    except UnicodeDecodeError as e:
        raise ValueError('Use a Trading 212 UTF-8 CSV export') from e
    if "\x00" in text:
        raise ValueError('Invalid CSV content: embedded null characters')
    reader = csv.DictReader(io.StringIO(text, newline=''))
    fields = reader.fieldnames or ()
    if (not fields or any(not name or not isinstance(name, str) for name in fields)
            or len(set(fields)) != len(fields)):
        raise ValueError('Missing or duplicate CSV column headers; export the original Trading 212 history')
    if not {'Action', 'Time'}.issubset(fields):
        raise ValueError('Missing Action/Time headers; choose Trading 212 history export')
    entries, errors, duplicates, seen = [], [], [], set()
    imported_rows = list(BrokerImportRow.objects.filter(strategy=strategy).select_related('ledger_entry'))
    existing_fp = {item.fingerprint for item in imported_rows}
    known_ids = {item.ledger_entry.note.removeprefix('Trading 212 CSV ID '): item.fingerprint
                 for item in imported_rows if item.ledger_entry.note.startswith('Trading 212 CSV ID ')
                 and item.ledger_entry.note != 'Trading 212 CSV ID '}
    batch_ids = {}
    for line, row in enumerate(reader, start=2):
        if None in row:
            errors.append((line, 'More CSV values than column headers; check the original export'))
            continue
        if line > 15002:
            errors.append((line, 'Maximum 15,000 rows per import'))
            break
        try:
            entry = _canonical(row, strategy)
            fp = entry['fingerprint']
            if fp in seen:
                if not entry['broker_id']:
                    errors.append((line, 'Ambiguous repeated event without a broker ID; reconcile before import'))
                else:
                    duplicates.append(line)
                continue
            if fp in existing_fp:
                duplicates.append(line)
                continue
            seen.add(fp)
            broker_id = entry['broker_id']
            if broker_id and ((broker_id in known_ids and known_ids[broker_id] != fp) or
                              (broker_id in batch_ids and batch_ids[broker_id] != fp)):
                errors.append((line, 'Broker event ID was reused with different details; reconcile correction before import'))
                continue
            if broker_id:
                batch_ids[broker_id] = fp
            match = strategy.ledger.filter(
                kind=entry['kind'], occurred_on=entry['occurred_on'],
                symbol=entry['symbol'], quantity=entry['quantity'],
                unit_price=entry['unit_price'], amount=entry['amount'], fee=entry['fee'],
            ).exists()
            if match:
                errors.append((line, 'Possible existing manual ledger entry; reconcile before import'))
                continue
            entries.append(entry)
        except ValueError as exc:
            errors.append((line, str(exc)))
    entries.sort(key=lambda x: (x['event_time'], 0 if x['kind']=='DEPOSIT' else 1))
    return entries, errors, duplicates


@login_required
@require_http_methods(['GET', 'POST'])
def csv_import(request):
    strategy, blocked = _strategy(request.user)
    if not strategy:
        return redirect('cockpit_today')
    ctx = {'strategy': strategy}
    if request.method == 'POST':
        uploaded = request.FILES.get('csv_file')
        if not uploaded:
            messages.error(request, 'Choose a Trading 212 CSV file.')
        else:
            try:
                # Applying requires a successful dry-run of the EXACT bytes in
                # the same authenticated session. The re-upload is still fully
                # validated rather than trusting client-supplied row data.
                if uploaded.size > 4_000_000:
                    raise ValueError('File over 4 MB; split export into smaller periods')
                digest = hashlib.sha256(uploaded.read()).hexdigest()
                uploaded.seek(0)
                preview_key = f'{strategy.pk}:{digest}'
                applying = request.POST.get('apply') == 'yes'
                if applying and request.session.get('cockpit_t212_preview') != preview_key:
                    raise ValueError('Preview this exact CSV successfully before confirming import')
                entries, errors, duplicates = inspect_csv(uploaded, strategy)
                ctx.update(
                    preview_ran=True, preview_ready=not errors,
                    preview=entries[:100], total=len(entries), errors=errors[:100],
                    error_total=len(errors), duplicate_total=len(duplicates),
                    preview_truncated=len(entries) > 100,
                    duplicate_lines=duplicates[:30],
                    reviewed_file_name=uploaded.name.rsplit('/', 1)[-1][:120],
                )
                if not applying:
                    if errors:
                        request.session.pop('cockpit_t212_preview', None)
                    else:
                        request.session['cockpit_t212_preview'] = preview_key
                else:
                    if errors:
                        raise ValueError('Nothing imported: resolve every CSV error first')
                    with transaction.atomic():
                        locked = type(strategy).objects.select_for_update().get(
                            pk=strategy.pk, owner=request.user)
                        # Close the gap between preview and writes: a different
                        # tab may have recorded an overlapping manual event.
                        uploaded.seek(0)
                        ready, concurrent_errors, concurrent_duplicates = inspect_csv(
                            uploaded, locked)
                        if concurrent_errors:
                            raise ValueError('The ledger changed since preview; preview again and reconcile overlaps')
                        saved = 0
                        for row in ready:
                            if BrokerImportRow.objects.filter(
                                    strategy=locked, fingerprint=row['fingerprint']).exists():
                                continue
                            ledger = LedgerEntry(**{key: row[key] for key in
                                ('kind', 'occurred_on', 'symbol', 'quantity', 'unit_price', 'amount', 'fee', 'note')})
                            ledger.full_clean(exclude=['strategy'])
                            validate_and_record(locked, ledger)
                            BrokerImportRow.objects.create(
                                strategy=locked, fingerprint=row['fingerprint'], ledger_entry=ledger)
                            saved += 1
                    request.session.pop('cockpit_t212_preview', None)
                    messages.success(request, f'Imported {saved} new rows. Reconcile cash and positions with Trading 212.')
                    return redirect('cockpit_ledger')
            except (ValueError, ValidationError, IntegrityError) as exc:
                request.session.pop('cockpit_t212_preview', None)
                messages.error(request, 'Import was rolled back. ' + (
                    str(exc) if not isinstance(exc, IntegrityError) else
                    'Duplicate or database constraint conflict.'))
    return render(request, 'csv_cockpit.html', ctx)
