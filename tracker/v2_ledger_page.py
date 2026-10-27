"""Ledger page (read side): entries table, rebalance history, totals and CSV export.

Editing lives in v2_ledger_views; this module never writes."""
import csv
from decimal import Decimal as D

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import (
    LedgerRevision, RebalanceExecution,
    SignalFill, StrategyInstance, StrategySignal)
from .services import ledger_summary
from .strategies.instruments import symbols as fund_symbols
from .v2_ledger_core import (
    effective_entries)
from .v2_views import _render


KIND_GROUPS = (
    ("all", "All"), ("DEPOSIT", "Deposits"), ("WITHDRAW", "Withdrawals"),
    ("BUY", "Buys"), ("SELL", "Sells"), ("DIVIDEND", "Dividends"), ("INTEREST", "Interest"), ("FEE", "Fees"),
    ("SPLIT", "Splits"),
)
TABS = {"entries", "rebalances", "history"}
# The All / Money / Trades tabs above the entries table.
FILTER_GROUPS = {
    "all": ("All", {"DEPOSIT", "WITHDRAW", "DIVIDEND", "INTEREST", "FEE", "BUY", "SELL", "SPLIT"}),
    "money": ("Money", {"DEPOSIT", "WITHDRAW", "DIVIDEND", "INTEREST", "FEE"}),
    "trades": ("Trades", {"BUY", "SELL", "SPLIT"}),
}


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


def _safe(value):
    value = str(value or "")
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def _export(strategy, entries):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        'attachment; filename="wealtharr-v2-ledger-%s.csv"' % strategy.pk
    )
    writer = csv.writer(response)
    unit = strategy.account.currency.lower()
    # GBP exports keep their existing schema. USD/EUR owners get accurate
    # headers; values are already stored in their account's local currency.
    writer.writerow([
        "entry_id", "revision", "status", "occurred_on", "kind", "symbol",
        "quantity", f"unit_price_{unit}", f"amount_{unit}", f"fee_{unit}", "note",
        "source", "created_at",
    ])
    for e in entries:
        writer.writerow([
            e.pk, e.version, "soft_deleted" if e.deleted else "active",
            e.occurred_on.isoformat(), e.kind, _safe(e.symbol),
            str(e.quantity), str(e.unit_price), str(e.amount), str(e.fee),
            _safe(e.note), "manual_rebalance" if e.linked_fill else "manual",
            e.created_at.isoformat(),
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
    if kind not in dict(KIND_GROUPS) and kind not in FILTER_GROUPS:
        kind = "all"
    search = request.GET.get("q", "").strip()[:100]
    order = request.GET.get("sort", "newest")
    if order not in ("oldest", "newest", "kind", "amount"):
        order = "newest"
    show_deleted = request.GET.get("deleted") == "1"
    entries = effective_entries(strategy, show_deleted=show_deleted)
    counts = {key: sum(1 for e in entries if e.kind in kinds) for key, (_, kinds) in FILTER_GROUPS.items()}
    if kind in FILTER_GROUPS:
        entries = [e for e in entries if e.kind in FILTER_GROUPS[kind][1]]
    elif kind != "all":
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
        ledger_symbols=fund_symbols(strategy),
        ledger_tab=tab, ledger_kind=kind, ledger_search=search,
        ledger_sort=order, ledger_deleted=show_deleted,
        ledger_entries=entries[:200], ledger_count=len(entries),
        ledger_kinds=KIND_GROUPS, ledger_revisions=revisions,
        ledger_filters=tuple((key, label, counts[key]) for key, (label, _) in FILTER_GROUPS.items()),
        ledger_rebalances=_rebalance_rows(strategy) if tab == "rebalances" else (),
        ledger_summary=summary, ledger_can_edit=_editable(strategy),
    )


def _cash_move(entry):
    if entry.kind in ("DEPOSIT", "DIVIDEND", "INTEREST"):
        return entry.amount
    if entry.kind in ("WITHDRAW", "FEE"):
        return -entry.amount
    if entry.kind == "SPLIT":
        return D("0")
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
