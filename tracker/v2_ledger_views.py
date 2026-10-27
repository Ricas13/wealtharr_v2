"""Phase 3 real, owner-scoped ledger UI and transaction services.

Edits are immutable LedgerRevision overlays, not updates to LedgerEntry.
Every POST confirms a previously verified, session-bound preview and rechecks
the entire financial timeline under a row lock. Historical signal/baseline
recalculation is never silently performed.
"""
from datetime import date
from decimal import Decimal as D, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import (
    LedgerEntry, LedgerRevision)
from .services import validate_and_record
from .strategies.instruments import symbol_labels, symbols as fund_symbols
from .v2_review_refresh import rebuild_value_history, retire_stale_reviews
from .v2_ledger_core import (
    EffectiveEntry, effective_entries, evidence_impact, find_entry,
    latest_deleted_payload, linked_source, replay, snapshot, state_digest,
    with_proposed_change)
from .v2_ledger_page import _editable, _get
from .v2_views import _render
from .v2_onboarding import _decimal, _date, isa_deposits
from .core.isa_allowance import estimate_allowance, tax_year_bounds


OPERATIONS = {"new", "edit", "delete", "restore"}


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
    if kind == "SPLIT":
        symbol = post.get("symbol", "").strip()
        if not symbol or symbol not in fund_symbols(strategy):
            raise ValidationError("Choose the fund that split.")
        payload["symbol"] = symbol
        ratio = _decimal(post.get("ratio", "") or post.get("quantity", ""), "split ratio",
                         precision=8, allow_zero=False)
        if ratio == 1:
            raise ValidationError("A split ratio of 1 changes nothing.")
        payload["quantity"] = str(ratio)
        return payload
    if kind in ("BUY", "SELL"):
        symbol = post.get("symbol", "").strip()
        if not symbol or symbol not in fund_symbols(strategy):
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
            post.get("unit_price", ""), f"actual {strategy.account.currency} execution price", precision=8,
            allow_zero=False,
        ))
        payload["fee"] = str(_decimal(post.get("fee", ""), f"actual {strategy.account.currency} fee"))
    else:
        payload["amount"] = str(_decimal(
            post.get("amount", ""), f"actual {strategy.account.currency} cash amount", allow_zero=False
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
    recorded = isa_deposits(strategy.owner, start, end)
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


def _add_entry(locked, actor, payload, report):
    """Append one new entry and its ADD revision inside the caller's transaction."""
    instance = LedgerEntry(
        kind=payload["kind"], occurred_on=date.fromisoformat(payload["occurred_on"]),
        symbol=payload["symbol"], quantity=D(payload["quantity"]),
        unit_price=D(payload["unit_price"]), amount=D(payload["amount"]),
        fee=D(payload["fee"]), note=payload["note"],
    )
    instance.full_clean(exclude=["strategy"])
    validate_and_record(locked, instance)
    LedgerRevision.objects.create(
        strategy=locked, entry=instance, actor=actor, version=1,
        operation="ADD", before=None, after=snapshot(instance),
        impact=report["impact"],
    )
    return instance


def _suggested_buy(strategy, query, values):
    """Pre-select a buy of a recommended fund from a "Record what I bought" link.

    Only the instrument and the kind are filled in, and only for a fund this
    strategy holds. Shares, price, fee and date are what the broker actually did,
    so they stay empty; the cash budget is shown as a hint, never entered.
    """
    symbol = query.get("symbol", "")
    if query.get("kind") != "BUY" or symbol not in fund_symbols(strategy):
        return None
    try:
        budget = D(query.get("budget", ""))
        budget = budget.quantize(D("0.01")) if budget.is_finite() and budget > 0 else None
    except InvalidOperation:
        budget = None
    values.update(kind="BUY", symbol=symbol)
    return {"symbol": symbol, "budget": budget}


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
        from .v2_go_live import setup_missing
        if not setup_missing(strategy):
            raise Http404("Legacy ledger edits are unavailable")
        messages.error(request, "Finish this strategy's setup first, then you can record entries.")
        return redirect(reverse("v2_instance_page", args=[strategy.pk, "strategy"]) + "#go-live")
    default = snapshot(old) if old else {
        "occurred_on": timezone.localdate().isoformat(), "kind": "DEPOSIT",
        "symbol": "", "quantity": "", "unit_price": "", "amount": "", "fee": "",
        "note": "",
    }
    view = {"preview": None, "form_values": default, "isa_warning": ""}
    if operation == "new" and request.method == "GET":
        view["suggested_buy"] = _suggested_buy(strategy, request.GET, default)
    error = ""
    if request.method == "POST":
        try:
            if request.POST.get("intent") == "save" and operation == "new":
                # New entries save in one step; edits and deletes keep the preview.
                payload = _input_payload(request.POST, strategy)
                view["form_values"] = payload
                with transaction.atomic():
                    locked = _get(request, instance_id, locked=True)
                    report = _validate_proposal(locked, operation, None, payload)
                    advisory = _isa_advisory(
                        locked, payload, request.POST.get("other_isa_subscriptions", ""))
                    entry = _add_entry(locked, request.user, payload, report)
                    retire_stale_reviews(locked, entry.occurred_on)
                    rebuild_value_history(locked, entry.occurred_on)
                messages.success(request, "Saved. You can edit or delete it from the Ledger.")
                if advisory:
                    messages.warning(request, advisory)
                back = request.POST.get("return_to")
                return redirect("v2_instance_page", instance_id=instance_id,
                                section=back if back in ("today", "ledger") else "ledger")
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
                        changed_on = _add_entry(locked, request.user, payload, report).occurred_on
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
                    rebuild_value_history(locked, changed_on)
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
        edit_symbols=tuple(symbol_labels(strategy).items()),
        edit_values=view["form_values"], edit_preview=view["preview"],
        edit_isa_warning=view["isa_warning"], edit_error=error,
        suggested_buy=view.get("suggested_buy"),
    )
