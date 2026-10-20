"""Phase 4: source-grounded Today page for one owner-owned strategy.

No evaluation, HTTP price request, broker link or trade is made by GET. This
adapter only presents persisted reviews, actual ledger balances and independently
verified quote-date valuation evidence. Existing 9Sig rules are not modified.
"""
from datetime import timedelta
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

from .cockpit import _last_action, _next_end, _price_status
from .cockpit_execution import (
    completion_rows, finalize_manual_review, proposed_actions, record_single_fill,
)
from .cockpit_forms import SingleSignalFillForm
from .core.presentation import account_value, holding_rows
from .instrument_mapping import for_strategy
from .market_signals import quarter_end_before
from .models import (
    LedgerRevision, RebalanceExecution, SignalFill, StrategyInstance,
    StrategySignal, StrategyValueSnapshot,
)
from .services import ledger_summary
from .v2_ledger_views import _unreconciled
from .v2_views import _render


ZERO = Decimal("0")


def _owned(request, instance_id):
    return get_object_or_404(
        StrategyInstance.objects.select_related("account", "preset").filter(
            owner=request.user, account__owner=request.user
        ).exclude(account__kind="PAPER"), pk=instance_id,
    )


def _ledger_corrections(strategy):
    # Phase 5 must explicitly replay and re-approve any historic signal after
    # an overlay revision. Suppress new trade guidance meanwhile.
    return LedgerRevision.objects.filter(
        strategy=strategy, operation__in=("EDIT", "DELETE", "RESTORE")
    ).exists()


def _readiness(strategy, report):
    if strategy.tracking_only:
        return False, (
            "This strategy is tracking-only: account-level instrument permissions "
            "and quote units have not been independently verified. No trade "
            "instructions are available."
        ), ()
    if _unreconciled(strategy):
        return False, (
            "Your owner-reported opening holdings are not reconciled against "
            "actual broker history. Import and verify the original transactions."
        ), ()
    if _ledger_corrections(strategy):
        return False, (
            "Your ledger has audited corrections. Historical quarterly reviews "
            "must be recalculated and approved separately before trade advice."
        ), ()
    fresh, why, marks = _price_status(strategy, report)
    if not fresh:
        return False, why, marks
    # A viable price is not proof a quarterly review exists. We refuse to
    # silently reinterpret old persisted signals with revised rule versions.
    if strategy.strategy_version or strategy.mapping_version:
        from .strategies.registry import describe_strategy
        from .instrument_mapping import MAPPING_VERSION
        desc = describe_strategy(strategy.preset.slug)
        if (desc is None or strategy.strategy_version != desc.version
                or strategy.mapping_version != MAPPING_VERSION):
            return False, "The pinned strategy or mapping version has changed.", marks
    return True, "", marks


def _signal_rows(strategy):
    signals = strategy.signals.filter(
        kind="QUARTERLY"
    ).order_by("-as_of", "-pk")[:8]
    executed = set(
        RebalanceExecution.objects.filter(
            strategy=strategy, signal__in=signals
        ).values_list("signal_id", flat=True)
    )
    linked = {}
    for signal_id in SignalFill.objects.filter(
        strategy=strategy, signal__in=signals
    ).values_list("signal_id", flat=True):
        linked[signal_id] = linked.get(signal_id, 0) + 1
    return tuple({
        "signal": s,
        "complete": s.pk in executed,
        "partial_count": linked.get(s.pk, 0),
        "has_trades": bool(proposed_actions(s)),
    } for s in signals)


def _snapshot_rows(strategy):
    # The hourly writer stores only quote-day, GBP-evidenced samples.
    # Values are labelled historical snapshots, never tick-by-tick prices.
    return tuple(strategy.value_snapshots.order_by("-as_of")[:12])


@login_required
@require_GET
def today_page(request, instance_id):
    strategy = _owned(request, instance_id)
    report = ledger_summary(strategy)
    ready, reason, marks = _readiness(strategy, report)
    notice = _last_action(strategy)
    progress = ()
    partial = False
    executed = False
    if notice and notice.kind == "QUARTERLY":
        executed = RebalanceExecution.objects.filter(
            signal=notice, strategy=strategy
        ).exists()
        try:
            fills = list(SignalFill.objects.filter(
                strategy=strategy, signal=notice
            ).select_related("ledger_entry").order_by("pk"))
            progress = completion_rows(notice, fills)
            partial = bool(fills)
        except ValidationError:
            reason = "Recorded fills need audited reconciliation."
            ready = False
    elif notice and notice.kind not in ("THIRTY_DOWN", "SPIKE_WATCH"):
        notice = None
    if notice and notice.kind == "QUARTERLY" and not partial and (
            strategy.ledger.filter(occurred_on__gt=notice.as_of).exists()
            or strategy.ledger_revisions.filter(
                operation__in=("EDIT", "DELETE", "RESTORE")
            ).exists()):
        ready = False
        reason = (
            "The ledger changed after this review. An updated, verified "
            "evaluation is needed before relying on its quantities."
        )
    if (notice and notice.kind == "QUARTERLY" and partial and
            not strategy.tracking_only and not _unreconciled(strategy)
            and not _ledger_corrections(strategy)):
        status = "record"
        headline = "Finish recording actual fills"
        explanation = (
            "A real transaction has already been recorded against this "
            "quarter. Continue the actual-fill record or finalize only after "
            "the required post-fill verified GBP closing prices are available. "
            "These are not fresh instructions to trade."
        )
    elif notice and notice.kind in ("THIRTY_DOWN", "SPIKE_WATCH"):
        status = "review"
        headline = notice.title
        explanation = notice.message
    elif not ready:
        status = "blocked"
        headline = "Cannot compute a reliable instruction"
        explanation = reason
    elif ready and notice and notice.kind == "QUARTERLY" and progress and not executed:
        status = "trade"
        headline = "Review the verified quarterly proposal"
        explanation = (
            "Check the dated proposal against your broker and decide yourself. "
            "Nothing is executed by Wealtharr. Record only fills you actually made."
        )
    else:
        status = "idle"
        headline = "Nothing to do right now"
        explanation = "No currently outstanding verified quarterly instruction."
    next_end = _next_end(timezone.localdate())
    price_date = min((m.as_of for m in marks if m is not None), default=None) if ready else None
    display_value = account_value(
        net_cash=report["cash"], portfolio_value=report["value"],
        prices_verified=ready, has_holdings=bool(report["positions"]),
    )
    if _unreconciled(strategy):
        display_value = None
    gain = (
        report["profit"] if ready and report["value"] is not None
        else None
    )
    symbol_roles = {
        strategy.equity_symbol: "Growth",
        strategy.safe_symbol: "Defensive",
    }
    return _render(
        request, page_key="instance", page_title="Today",
        instance=strategy, section="today",
        today_status=status, today_headline=headline,
        today_explanation=explanation, today_blocking_reason=reason,
        today_ready=ready, today_signal=notice, today_partial=partial,
        today_progress=progress if (status in ("trade", "record")) else (),
        today_value=display_value, today_gain=gain,
        today_cash=report["cash"], today_contributed=report["net_contributed"],
        today_quote_date=price_date, today_next_review=next_end,
        today_days_to_review=(next_end-timezone.localdate()).days,
        today_holdings=holding_rows(report, roles=symbol_roles, show_quotes=ready),
        today_snapshots=() if _ledger_corrections(strategy) else _snapshot_rows(strategy),
        today_quarters=_signal_rows(strategy),
        today_chart_caution=(
            "Only persisted GBP-evidenced valuation dates are listed. "
            "A return rate is withheld until audited money-flow metrics are available."
        ),
    )


def _review_context(strategy, notice, *, form=None, error=""):
    from .v2_ledger_core import effective_entries
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
    current = notice.as_of == quarter_end_before(timezone.localdate())
    blocked = (
        strategy.tracking_only or _unreconciled(strategy)
        or _ledger_corrections(strategy) or
        not current or completed or
        notice.review_outcome in ("FALSE_POSITIVE", "NOT_APPLICABLE")
    )
    return {
        "review_signal": notice, "review_rows": rows,
        "review_completed": completed, "review_is_current": current,
        "review_blocked": blocked,
        "review_can_submit": not blocked and bool(rows),
        "review_can_finalize": not blocked and bool(rows)
                               and all(r["complete"] for r in rows),
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
    context = _review_context(strategy, notice)
    if request.method == "POST":
        if context["review_blocked"]:
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
            if intent == "record_one":
                form = SingleSignalFillForm(request.POST)
                if not form.is_valid():
                    raise ValidationError(
                        "Enter actual executed date, share quantity, GBP price "
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
                    "Actual fill recorded. The review remains partial until "
                    "a separate verified finalization."
                )
                return redirect(
                    "v2_guided_review", instance_id=strategy.pk, signal_id=notice.pk,
                )
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
                    "post-fill GBP closes. No broker order was placed."
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
        page_title="Record actual trades", instance=strategy,
        section="today", guided_review=True, **context,
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
