"""Guided review: the review page, recording actual fills, and price evidence."""
from decimal import Decimal
from uuid import uuid4

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .cockpit_execution import (
    accept_action_as_done, completion_rows, finalize_manual_review, late_review_allowed, proposed_actions,
    record_single_fill,
)
from .cockpit_forms import SingleSignalFillForm
from .models import RebalanceExecution, SignalFill, StrategyInstance, StrategySignal
from .strategies.registry import is_current_review, module_for
from .v2_views import _render
from .v2_today import _owned


ZERO = Decimal("0")



def _review_context(strategy, notice, *, form=None, error=""):
    partial_fills = list(SignalFill.objects.filter(
        strategy=strategy, signal=notice
    ).select_related("ledger_entry").order_by("pk"))
    try:
        rows = completion_rows(notice, partial_fills)
    except ValidationError as exc:
        rows = ()
        error = "; ".join(exc.messages)
    completed = RebalanceExecution.objects.filter(
        strategy=strategy, signal=notice
    ).exists()
    current = is_current_review(strategy, notice.as_of, timezone.localdate())
    late = not current and not completed and late_review_allowed(strategy, notice)
    blocked = (
        strategy.tracking_only or
        not current or completed or
        notice.review_outcome in ("FALSE_POSITIVE", "NOT_APPLICABLE")
    )
    plugin = module_for(strategy)
    can_manual_reset = bool(
        not blocked and not partial_fills and not notice.reset_reason
        and plugin and plugin.offers_manual_reset(notice)
    )
    return {
        "review_signal": notice, "review_rows": rows,
        "review_completed": completed, "review_is_current": current,
        "review_blocked": blocked,
        "review_can_submit": not blocked and bool(rows),
        "review_can_manual_reset": can_manual_reset,
        "review_can_finalize": (not blocked or (late and not strategy.tracking_only))
                               and bool(rows) and all(r["complete"] for r in rows),
        "review_late": late,
        "review_form": form, "review_error": error,
        "review_nonce": uuid4(),
        "review_actual_fills": partial_fills,
    }


@login_required
@require_http_methods(["GET", "POST"])
def guided_review(request, instance_id, signal_id):
    strategy = _owned(request, instance_id)
    notice = get_object_or_404(
        StrategySignal, pk=signal_id, strategy=strategy, kind="QUARTERLY",
    )
    if request.method == "GET" and request.GET.get("export") == "csv":
        plugin = module_for(strategy)
        if not (plugin and plugin.review_export):
            raise Http404("No calculation export for this strategy")
        return plugin.review_export(strategy, notice)
    context = _review_context(strategy, notice)
    if request.method == "POST":
        late_finish = request.POST.get("intent") == "finalize" and context["review_can_finalize"]
        if context["review_blocked"] and not late_finish:
            messages.error(
                request,
                "This review is historical, completed, tracking-only, "
                "or requires audited reconciliation. No ledger entry was saved."
            )
            return redirect(
                "v2_guided_review", instance_id=strategy.pk, signal_id=notice.pk,
            )
        intent = request.POST.get("intent", "")
        try:
            if intent == "request_manual_reset":
                if not context["review_can_manual_reset"]:
                    raise ValidationError("This saved review cannot offer a manual reset.")
                chosen = module_for(strategy).manual_reset(
                    owner=request.user, strategy_id=strategy.pk, signal_id=notice.pk,
                )
                messages.success(
                    request, "Separate 60/40 review created. No trade or reset was executed."
                )
                return redirect(
                    "v2_guided_review", instance_id=strategy.pk, signal_id=chosen.pk,
                )
            if intent == "record_one":
                form = SingleSignalFillForm(request.POST)
                if not form.is_valid():
                    raise ValidationError(
                        "Enter actual executed date, share quantity, price "
                        "and fees with a valid confirmation token."
                    )
                if request.POST.get("actual_confirmation") != "yes":
                    raise ValidationError(
                        "Confirm that this transaction actually occurred in your broker account."
                    )
                d = form.cleaned_data
                outcome, _ = record_single_fill(
                    owner=request.user, strategy_id=strategy.pk,
                    signal_id=notice.pk, action_index=d["action_index"],
                    executed_on=d["executed_on"], quantity=d["quantity"],
                    unit_price=d["unit_price"], fee=d["fee"],
                    idempotency_key=d["idempotency_key"],
                )
                messages.success(
                    request,
                    "Actual fill already recorded; no duplicate was added."
                    if outcome == "duplicate" else
                    "Actual fill recorded. Once every trade is recorded, the review "
                    "finishes automatically after the next closing prices."
                )
                return redirect(
                    "v2_guided_review", instance_id=strategy.pk, signal_id=notice.pk,
                )
            if intent == "accept_action":
                try:
                    index = int(request.POST.get("action_index", ""))
                except ValueError:
                    raise ValidationError("Select an action from the current review.")
                accept_action_as_done(owner=request.user, strategy_id=strategy.pk, signal_id=notice.pk,
                                      action_index=index, note=request.POST.get("note", ""))
                messages.success(request, "Marked done with the shares you recorded. The difference is noted on the review.")
                return redirect("v2_guided_review", instance_id=strategy.pk, signal_id=notice.pk)
            if intent == "finalize":
                if not context["review_can_finalize"]:
                    raise ValidationError(
                        "Complete each actual fill before finalizing the review."
                    )
                finalize_manual_review(
                    owner=request.user, strategy_id=strategy.pk,
                    signal_id=notice.pk,
                )
                messages.success(
                    request,
                    "Review completed against actual fills and verified "
                    "post-fill closing prices. No broker order was placed."
                )
                return redirect(
                    "v2_instance_page", instance_id=strategy.pk, section="today",
                )
            raise ValidationError("Unknown review action; nothing was saved.")
        except (ValidationError, IntegrityError, ValueError) as exc:
            err = "; ".join(exc.messages) if isinstance(exc, ValidationError) else (
                "Could not confirm this fill; nothing was saved."
            )
            context = _review_context(strategy, notice, error=err)
    return _render(
        request, page_key="instance",
        page_title="Quarterly review", instance=strategy,
        section="review", guided_review=True,
        **_review_extras(strategy, notice), **context,
    )


def _review_extras(strategy, notice):
    """Calculation steps and past reviews shown beside the fills form."""
    plugin = module_for(strategy)
    past = []
    for signal in strategy.signals.filter(kind="QUARTERLY").order_by("-as_of", "-pk")[:12]:
        if notice and signal.pk == notice.pk:
            continue
        trades = proposed_actions(signal)
        execution = RebalanceExecution.objects.filter(signal=signal).first()
        fills = list(SignalFill.objects.filter(signal=signal).select_related("ledger_entry"))
        past.append({
            "signal": signal, "trades": trades,
            "suggested": sum((Decimal(str(a.get("estimated_gbp") or 0)) for a in trades), ZERO),
            "recorded": sum((f.ledger_entry.quantity * f.ledger_entry.unit_price for f in fills), ZERO),
            "filled_on": max((f.ledger_entry.occurred_on for f in fills), default=None),
            "execution": execution,
        })
    detail = (plugin.review_detail(strategy, notice)
              if notice and plugin and plugin.review_detail else None)
    return {
        "review_detail": detail,
        "review_evidence": price_evidence(notice) if notice else (),
        "review_past": tuple(past),
        "review_trades": proposed_actions(notice) if notice else (),
    }


def price_evidence(signal):
    """Each close behind a proposal, with where it came from.

    Looked up in the stored observations for the account's currency, so it shows
    what the evidence is now, not just what the review text claims. A stored
    close that differs from the price the proposal used is flagged. The broker's
    dealing price may differ either way.
    """
    from .market_data import is_verified
    from .models import MarketObservation
    currency = signal.strategy.account.currency
    rows = []
    for action in proposed_actions(signal):
        symbol, day = action.get("symbol"), action.get("price_date")
        found = (MarketObservation.objects.filter(symbol=symbol, as_of=day, currency=currency).first()
                 if symbol and day else None)
        try:
            used = Decimal(str(action["price_gbp"])) if action.get("price_gbp") else None
        except Exception:
            used = None
        matches = None if (found is None or used is None) else found.price == used
        rows.append({
            "side": action.get("side", ""), "symbol": symbol, "date": day,
            "price": used if used is not None else (found.price if found else None),
            "price_is_stored": used is None and found is not None,
            "source": found.source if found else "", "unit": found.raw_quote_unit if found else "",
            "raw_close": found.raw_close if found else None,
            "matches": matches,
            "verified": is_verified(found) and matches is not False,
            "found": found is not None,
        })
    return tuple(rows)


@login_required
@require_GET
def review_page(request, instance_id):
    """The current quarter's review, or the latest one, with its calculation."""
    strategy = _owned(request, instance_id)
    signals = list(strategy.signals.filter(kind="QUARTERLY").exclude(
        review_outcome__in=("FALSE_POSITIVE", "NOT_APPLICABLE")).order_by("-as_of", "-pk"))
    notice = signals[0] if signals else None
    context = _review_context(strategy, notice) if notice else {"review_signal": None}
    return _render(
        request, page_key="instance", page_title="Quarterly review",
        instance=strategy, section="review", guided_review=bool(notice),
        **_review_extras(strategy, notice), **context,
    )


@login_required
@require_POST
def notice_feedback(request, instance_id, signal_id):
    """Record owner assessment of saved advice; NEVER treat it as a broker fill.

    Historical alerts remain intact. A signal with actual linked fills or
    confirmed execution cannot be reclassified by this lightweight feedback.
    The strategy row is locked first, matching the execution service order.
    """
    strategy = _owned(request, instance_id)
    outcome = request.POST.get("outcome", "")
    note = request.POST.get("review_note", "").strip()[:2000]
    if outcome not in ("CONFIRMED", "FALSE_POSITIVE", "NOT_APPLICABLE"):
        messages.error(request, "Choose a valid review outcome.")
        return redirect("v2_instance_page", instance_id=strategy.pk, section="today")
    if outcome == "FALSE_POSITIVE" and not note:
        messages.error(request, "Give a short reason for a false positive.")
        return redirect("v2_instance_page", instance_id=strategy.pk, section="today")
    with transaction.atomic():
        locked = _owned(request, instance_id)
        StrategyInstance.objects.select_for_update().get(pk=locked.pk)
        signal = get_object_or_404(
            StrategySignal.objects.select_for_update(),
            pk=signal_id, strategy=locked,
        )
        if (SignalFill.objects.filter(strategy=locked, signal=signal).exists()
                or RebalanceExecution.objects.filter(strategy=locked, signal=signal).exists()):
            messages.error(request, "Actual fills exist. Review them through the audited trade record.")
            return redirect("v2_instance_page", instance_id=locked.pk, section="today")
        signal.review_outcome = outcome
        signal.review_note = note
        signal.acknowledged_at = timezone.now()
        signal.save(update_fields=["review_outcome", "review_note", "acknowledged_at"])
    messages.success(request, "Review recorded. No trades or balances changed.")
    return redirect("v2_instance_page", instance_id=strategy.pk, section="today")
