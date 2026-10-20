"""Phase 3 real, owner-scoped ledger UI and transaction services.

Edits are immutable LedgerRevision overlays, not updates to LedgerEntry.
Every POST confirms a previously verified, session-bound preview and rechecks
the entire financial timeline under a row lock. Historical signal/baseline
recalculation is never silently performed.
"""
import csv
from datetime import date
from decimal import Decimal as D, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import (
    InstanceOpeningSnapshot, LedgerEntry,
    LedgerRevision, OpeningReconciliation, RebalanceExecution,
    SignalFill, StrategyInstance, StrategySignal,
)
from .services import ledger_summary, validate_and_record
from .v2_review_refresh import retire_stale_reviews
from .v2_ledger_core import (
    ZERO, EffectiveEntry, effective_entries, evidence_impact, find_entry,
    latest_deleted_payload, linked_source, replay, snapshot, state_digest,
    with_proposed_change,
)
from .v2_views import _render
from .v2_onboarding import OPENING_NOTE, _decimal, _date
from .core.isa_allowance import estimate_allowance, tax_year_bounds


KIND_GROUPS = (
    ("all", "All"), ("DEPOSIT", "Deposits"), ("WITHDRAW", "Withdrawals"),
    ("BUY", "Buys"), ("SELL", "Sells"), ("DIVIDEND", "Dividends"), ("INTEREST", "Interest"), ("FEE", "Fees"),
)
TABS = {"entries", "rebalances", "history"}
OPERATIONS = {"new", "edit", "delete", "restore"}


def _get(request, instance_id, *, locked=False):
    qs = StrategyInstance.objects.select_related("account", "preset").filter(
        pk=instance_id, owner=request.user, account__owner=request.user
    ).exclude(account__kind="PAPER")
    if locked:
        qs = qs.select_for_update()
    return get_object_or_404(qs)


def _editable(strategy):
    return bool(
        strategy.strategy_key and strategy.started_on and
        strategy.resolved_instruments
    )


def _unreconciled(strategy):
    # Opening holdings are now entered as ledger entries, and there is no
    # broker import to reconcile against, so nothing is ever "unreconciled".
    return False


def _safe(value):
    value = str(value or "")
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def _export(strategy, entries):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        'attachment; filename="wealtharr-v2-ledger-%s.csv"' % strategy.pk
    )
    writer = csv.writer(response)
    writer.writerow([
        "entry_id", "revision", "status", "occurred_on", "kind", "symbol",
        "quantity", "unit_price_gbp", "amount_gbp", "fee_gbp", "note",
        "source", "created_at",
    ])
    for e in entries:
        writer.writerow([
            e.pk, e.version, "soft_deleted" if e.deleted else "active",
            e.occurred_on.isoformat(), e.kind, _safe(e.symbol),
            str(e.quantity), str(e.unit_price), str(e.amount), str(e.fee),
            _safe(e.note), "broker_csv" if e.imported else (
                "manual_rebalance" if e.linked_fill else "manual"
            ), e.created_at.isoformat(),
        ])
    return response


def _rebalance_rows(strategy):
    from .cockpit_execution import completion_rows
    result = []
    signals = list(StrategySignal.objects.filter(
        strategy=strategy, kind="QUARTERLY"
    ).order_by("-as_of", "-pk")[:100])
    executions = {
        x.signal_id: x for x in RebalanceExecution.objects.filter(
            strategy=strategy, signal_id__in=[s.pk for s in signals]
        )
    }
    fills = list(SignalFill.objects.filter(
        strategy=strategy, signal_id__in=[s.pk for s in signals]
    ).select_related("ledger_entry").order_by("pk"))
    by_signal = {}
    for fill in fills:
        by_signal.setdefault(fill.signal_id, []).append(fill)
    for signal in signals:
        try:
            lines = completion_rows(signal, by_signal.get(signal.pk, []))
            warning = ""
        except ValidationError:
            lines = ()
            warning = "Linked execution records require reconciliation."
        result.append({
            "signal": signal, "rows": lines,
            "recorded_fills": by_signal.get(signal.pk, []),
            "execution": executions.get(signal.pk),
            "warning": warning,
        })
    return result


@login_required
@require_http_methods(["GET"])
def ledger_page(request, instance_id):
    strategy = _get(request, instance_id)
    tab = request.GET.get("tab", "entries")
    if tab not in TABS:
        tab = "entries"
    kind = request.GET.get("kind", "all")
    if kind not in dict(KIND_GROUPS):
        kind = "all"
    search = request.GET.get("q", "").strip()[:100]
    order = request.GET.get("sort", "newest")
    if order not in ("oldest", "newest", "kind", "amount"):
        order = "newest"
    show_deleted = request.GET.get("deleted") == "1"
    entries = effective_entries(strategy, show_deleted=show_deleted)
    if kind != "all":
        entries = [e for e in entries if e.kind == kind]
    if search:
        entries = [
            e for e in entries if search.casefold() in (
                " ".join([e.kind, e.symbol, e.note, str(e.quantity), str(e.amount)])
            ).casefold()
        ]
    if order == "oldest":
        entries.sort(key=lambda e: (e.occurred_on, e.pk))
    elif order == "kind":
        entries.sort(key=lambda e: (e.kind, -e.pk))
    elif order == "amount":
        entries.sort(key=lambda e: (max(e.amount, e.financial_value), e.pk), reverse=True)
    else:
        entries.sort(key=lambda e: (e.occurred_on, e.pk), reverse=True)
    if request.GET.get("export") == "csv":
        return _export(strategy, entries)
    revisions = list(
        LedgerRevision.objects.filter(strategy=strategy)
        .select_related("actor", "entry")
        .order_by("-created_at", "-pk")[:200]
    ) if tab == "history" else []
    try:
        summary = ledger_summary(strategy)
    except (ValidationError, ArithmeticError):
        summary = None
    running = _running_cash(strategy)
    return _render(
        request, page_key="instance", page_title="Ledger",
        section="ledger", instance=strategy,
        ledger_rows=tuple({"entry": e, **running.get(e.pk, {})} for e in entries[:200]),
        ledger_totals=_totals(strategy),
        ledger_recent_changes=LedgerRevision.objects.filter(strategy=strategy).exclude(
            operation="ADD").select_related("entry").order_by("-created_at", "-pk")[:3],
        ledger_today=timezone.localdate(),
        ledger_symbols=tuple(x for x in (strategy.equity_symbol, strategy.safe_symbol) if x),
        ledger_tab=tab, ledger_kind=kind, ledger_search=search,
        ledger_sort=order, ledger_deleted=show_deleted,
        ledger_entries=entries[:200], ledger_count=len(entries),
        ledger_kinds=KIND_GROUPS, ledger_revisions=revisions,
        ledger_rebalances=_rebalance_rows(strategy) if tab == "rebalances" else (),
        ledger_summary=summary, ledger_can_edit=_editable(strategy),
        ledger_unreconciled=_unreconciled(strategy),
    )


def _cash_move(entry):
    if entry.kind in ("DEPOSIT", "DIVIDEND", "INTEREST"):
        return entry.amount
    if entry.kind in ("WITHDRAW", "FEE"):
        return -entry.amount
    gross = (entry.quantity * entry.unit_price).quantize(D("0.01"))
    return -(gross + entry.fee) if entry.kind == "BUY" else gross - entry.fee


def _running_cash(strategy):
    """Cash movement and cash balance after each effective entry."""
    cash = D("0")
    result = {}
    for entry in sorted(effective_entries(strategy), key=lambda e: (e.occurred_on, e.pk)):
        move = _cash_move(entry)
        cash += move
        result[entry.pk] = {"move": move, "after": cash}
    return result


def _totals(strategy):
    totals = {"deposits": D("0"), "withdrawals": D("0"), "income": D("0"),
              "fees": D("0"), "bought": D("0"), "sold": D("0")}
    for entry in effective_entries(strategy):
        gross = (entry.quantity * entry.unit_price).quantize(D("0.01"))
        if entry.kind == "DEPOSIT":
            totals["deposits"] += entry.amount
        elif entry.kind == "WITHDRAW":
            totals["withdrawals"] += entry.amount
        elif entry.kind in ("DIVIDEND", "INTEREST"):
            totals["income"] += entry.amount
        elif entry.kind == "FEE":
            totals["fees"] += entry.amount
        elif entry.kind == "BUY":
            totals["bought"] += gross
            totals["fees"] += entry.fee
        elif entry.kind == "SELL":
            totals["sold"] += gross
            totals["fees"] += entry.fee
    return totals


def _input_payload(post, strategy):
    kind = post.get("kind", "").upper().strip()
    if kind not in dict(LedgerEntry.TYPES):
        raise ValidationError("Choose a supported ledger event type.")
    day = _date(post.get("occurred_on", ""))
    if strategy.started_on and day < strategy.started_on:
        raise ValidationError("Entries can't be dated before this strategy's start date.")
    note = str(post.get("note", "")).strip()
    if len(note) > 280:
        raise ValidationError("Note must not exceed 280 characters.")
    payload = {
        "kind": kind, "occurred_on": day.isoformat(),
        "symbol": "", "quantity": "0", "unit_price": "0", "amount": "0",
        "fee": "0", "note": note,
    }
    if kind in ("BUY", "SELL"):
        symbol = post.get("symbol", "").strip()
        if symbol not in (strategy.equity_symbol, strategy.safe_symbol) or not symbol:
            raise ValidationError("Choose one exact mapped trading line for this strategy.")
        if symbol not in {
            role.get("yahoo_ticker") for role in
            (strategy.resolved_instruments or {}).get("roles", {}).values()
        }:
            raise ValidationError("Ticker no longer matches the pinned instrument manifest.")
        payload["symbol"] = symbol
        payload["quantity"] = str(_decimal(
            post.get("quantity", ""), "actual shares", precision=8, allow_zero=False
        ))
        payload["unit_price"] = str(_decimal(
            post.get("unit_price", ""), "actual GBP execution price", precision=8,
            allow_zero=False,
        ))
        payload["fee"] = str(_decimal(post.get("fee", ""), "actual GBP fee"))
    else:
        payload["amount"] = str(_decimal(
            post.get("amount", ""), "actual GBP cash amount", allow_zero=False
        ))
    return payload


def _candidate(strategy, payload, pk=1_000_000_000):
    return EffectiveEntry(
        pk=pk, strategy_id=strategy.pk,
        occurred_on=date.fromisoformat(payload["occurred_on"]),
        kind=payload["kind"], symbol=payload["symbol"],
        quantity=D(payload["quantity"]), unit_price=D(payload["unit_price"]),
        amount=D(payload["amount"]), fee=D(payload["fee"]),
        note=payload["note"], created_at=timezone.now(),
    )


def _validate_proposal(strategy, operation, entry_id, payload):
    if _unreconciled(strategy):
        raise ValidationError(
            "Reconcile the complete existing holdings using your Trading 212 CSV first."
        )
    if not _editable(strategy):
        raise ValidationError("Only pinned v2 strategy instances support audited edits.")
    current_rows = effective_entries(strategy, show_deleted=True)
    record = find_entry(strategy, entry_id) if entry_id is not None else None
    if operation != "new" and record is None:
        raise ValidationError("Entry not found in this account.")
    if record and linked_source(strategy, record.pk):
        raise ValidationError("Imported transactions and confirmed fills must be reconciled at their original source.")
    if operation == "edit" and record.deleted:
        raise ValidationError("Restore this deleted event before editing.")
    if operation == "delete" and record.deleted:
        raise ValidationError("This entry is already deleted.")
    if operation == "restore" and not record.deleted:
        raise ValidationError("Only deleted entries can be restored.")
    new_day = date.fromisoformat(payload["occurred_on"]) if payload else None
    earliest = min(filter(None, [record.occurred_on if record else None, new_day]))
    impact = evidence_impact(strategy, earliest)
    if impact["blocks_change"]:
        raise ValidationError(
            "This date is before a confirmed 60/40 reset. Entries before a reset "
            "can't be changed; record a new entry dated today instead."
        )
    if operation == "new":
        after = replay(
            [e for e in current_rows if not e.deleted] +
            [_candidate(strategy, payload)], strict=True
        )
        before = replay(current_rows, strict=True)
    else:
        if operation == "restore":
            if payload is None:
                raise ValidationError("Restore requires the original reviewed values.")
        before = replay(current_rows, strict=True)
        _, after = with_proposed_change(strategy, entry_id, payload)
    return {
        "before": before, "after": after, "impact": impact,
        "entry": snapshot(record) if record and not record.deleted else None,
        "replacement": payload,
    }


def _isa_advisory(strategy, payload, other_isa=""):
    """Advisory only: never reject an actual deposit based on unseen ISA data."""
    if not payload or payload["kind"] != "DEPOSIT" or strategy.account.kind != "ISA":
        return ""
    other_raw = str(other_isa or "").strip()
    other = _decimal(other_raw, "other ISA subscriptions") if other_raw else None
    day = date.fromisoformat(payload["occurred_on"])
    start, end = tax_year_bounds(day)
    recorded = tuple(strategy.ledger.filter(
        kind=LedgerEntry.DEPOSIT, occurred_on__range=(start, end),
    ).exclude(note=OPENING_NOTE).values_list("occurred_on", "amount"))
    try:
        estimate = estimate_allowance(
            as_of=day,
            recorded_deposits=recorded + ((day, D(payload["amount"])),),
            other_isa_subscriptions=other,
        )
    except ValueError:
        return "ISA allowance policy is not verified for this tax year; check HMRC."
    return estimate.warning if estimate.status in ("approaching", "limit_reached") else ""


def _preview_key(strategy):
    return f"wealtharr_v2_ledger_preview_{strategy.pk}"


@login_required
@require_http_methods(["GET", "POST"])
def edit_entry(request, instance_id, operation, entry_id=None):
    if operation not in OPERATIONS or (entry_id is None) != (operation == "new"):
        raise Http404("Unknown ledger operation")
    strategy = _get(request, instance_id)
    old = find_entry(strategy, entry_id) if entry_id is not None else None
    if entry_id is not None and old is None:
        raise Http404("Entry not found")
    if not _editable(strategy):
        raise Http404("Legacy ledger edits are unavailable")
    default = snapshot(old) if old else {
        "occurred_on": timezone.localdate().isoformat(), "kind": "DEPOSIT",
        "symbol": "", "quantity": "", "unit_price": "", "amount": "", "fee": "",
        "note": "",
    }
    view = {"preview": None, "form_values": default, "isa_warning": ""}
    error = ""
    if request.method == "POST":
        try:
            if request.POST.get("intent") == "confirm":
                with transaction.atomic():
                    locked = _get(request, instance_id, locked=True)
                    key = _preview_key(locked)
                    approved = request.session.get(key)
                    if not approved or approved.get("operation") != operation or approved.get("entry_id") != entry_id:
                        raise ValidationError("First preview the exact revision you want to confirm.")
                    if approved["ledger_hash"] != state_digest(locked):
                        raise ValidationError("The ledger changed since preview. Review its impact again.")
                    payload = approved["payload"]
                    report = _validate_proposal(locked, operation, entry_id, payload)
                    advisory = _isa_advisory(
                        locked, payload, approved.get("other_isa_subscriptions", ""),
                    ) if operation == "new" else ""
                    if operation == "new":
                        instance = LedgerEntry(
                            kind=payload["kind"], occurred_on=date.fromisoformat(payload["occurred_on"]),
                            symbol=payload["symbol"], quantity=D(payload["quantity"]),
                            unit_price=D(payload["unit_price"]), amount=D(payload["amount"]),
                            fee=D(payload["fee"]), note=payload["note"],
                        )
                        instance.full_clean(exclude=["strategy"])
                        validate_and_record(locked, instance)
                        LedgerRevision.objects.create(
                            strategy=locked, entry=instance, actor=request.user, version=1,
                            operation="ADD", before=None, after=snapshot(instance),
                            impact=report["impact"],
                        )
                        changed_on = instance.occurred_on
                    else:
                        prior = find_entry(locked, entry_id)
                        current_revision = LedgerRevision.objects.filter(
                            strategy=locked, entry_id=entry_id
                        ).order_by("-version").first()
                        LedgerRevision.objects.create(
                            strategy=locked, entry_id=entry_id, actor=request.user,
                            version=(current_revision.version + 1) if current_revision else 1,
                            operation=operation.upper(),
                            before=snapshot(prior) if not prior.deleted else None,
                            after=payload,
                            impact=report["impact"],
                            reason=request.POST.get("reason", "").strip()[:280],
                        )
                        dates = [prior.occurred_on]
                        if payload:
                            dates.append(date.fromisoformat(payload["occurred_on"]))
                        changed_on = min(dates)
                    # Only reviews dated on/after the changed entry are retired.
                    retire_stale_reviews(locked, changed_on)
                    request.session.pop(key, None)
                messages.success(request, "Ledger change saved with its full revision history.")
                if advisory:
                    messages.warning(request, advisory)
                return redirect("v2_instance_page", instance_id=instance_id, section="ledger")
            payload = (
                _input_payload(request.POST, strategy) if operation in ("new", "edit")
                else latest_deleted_payload(strategy, entry_id) if operation == "restore"
                else None
            )
            view["form_values"] = payload or default
            report = _validate_proposal(strategy, operation, entry_id, payload)
            other_isa = (
                request.POST.get("other_isa_subscriptions", "").strip()
                if operation == "new" else ""
            )
            view["isa_warning"] = _isa_advisory(
                strategy, payload, other_isa,
            ) if operation == "new" else ""
            request.session[_preview_key(strategy)] = {
                "operation": operation, "entry_id": entry_id,
                "payload": payload, "ledger_hash": state_digest(strategy),
                "other_isa_subscriptions": other_isa,
            }
            view["preview"] = report
        except (ValidationError, ValueError, InvalidOperation, IntegrityError) as exc:
            error = (
                exc.messages[0] if isinstance(exc, ValidationError) else
                "The ledger change could not be validated. Nothing was saved."
            )
            request.session.pop(_preview_key(strategy), None)
    return _render(
        request, page_key="instance",
        page_title={"new": "Add ledger entry", "edit": "Edit ledger entry",
                    "delete": "Delete ledger entry", "restore": "Restore ledger entry"}[operation],
        instance=strategy, section="ledger",
        ledger_edit=True, edit_operation=operation, edit_entry=old,
        edit_values=view["form_values"], edit_preview=view["preview"],
        edit_isa_warning=view["isa_warning"], edit_error=error,
    )
