"""Phase 1: owner-scoped, strategy-neutral v2 page shell.

The financial cockpit under /cockpit/ remains authoritative until later
phases replace its pages. No values, strategy evaluations or trades are
fabricated by these placeholder views.
"""
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from .models import StrategyInstance
from .core.catalogue import CATALOGUE, OFFERS, ACCOUNT_TYPES, CATALOGUE_VERSION


SECTIONS = ("today", "history", "ledger", "strategy", "breakdown")
LABELS = {
    "today": "Today",
    "history": "History",
    "ledger": "Ledger",
    "strategy": "Strategy",
    "breakdown": "Breakdown",
}


def _instances(request):
    return list(
        StrategyInstance.objects.filter(
            owner=request.user, account__owner=request.user
        ).exclude(account__kind="PAPER").select_related(
            "account", "preset"
        ).order_by("created_at", "pk")
    )


def _render(request, *, page_key, page_title, instance=None, section=None, **extra):
    items = _instances(request)
    return render(
        request, "v2/page.html",
        {
            "shell_instances": items,
            "selected_instance": instance,
            "section": section,
            "page_key": page_key,
            "page_title": page_title,
            "instance_count": len(items),
            **extra,
        },
    )


@login_required
def overview(request):
    return _render(request, page_key="overview", page_title="Overview")


@login_required
def instance_page(request, instance_id, section):
    if section not in SECTIONS:
        raise Http404("Unknown strategy page")
    if section == "ledger":
        from .v2_ledger_views import ledger_page
        return ledger_page(request, instance_id)
    if section == "today":
        from .v2_today import today_page
        return today_page(request, instance_id)
    instance = get_object_or_404(
        StrategyInstance.objects.select_related("account", "preset"),
        pk=instance_id,
        owner=request.user,
        account__owner=request.user,
    )
    if instance.account.kind == "PAPER":
        raise Http404("Unsupported account type")
    return _render(
        request, page_key="instance", page_title=LABELS[section],
        instance=instance, section=section,
    )


@login_required
def catalogue(request):
    strategy_filter = request.GET.get("strategy", "")
    type_filter = request.GET.get("account_type", "")
    if strategy_filter not in OFFERS:
        strategy_filter = ""
    if type_filter not in dict(ACCOUNT_TYPES):
        type_filter = ""
    rows = tuple(row for row in CATALOGUE.values()
                 if (not strategy_filter or row.strategy_key == strategy_filter)
                 and (not type_filter or row.account_type == type_filter))
    return _render(
        request, page_key="catalogue", page_title="Instrument catalogue",
        catalogue_rows=rows, catalogue_offers=OFFERS.values(),
        catalogue_types=ACCOUNT_TYPES, catalogue_filter=strategy_filter,
        catalogue_type_filter=type_filter, catalogue_version=CATALOGUE_VERSION,
    )


@login_required
def settings(request):
    return _render(request, page_key="settings", page_title="Settings")


@login_required
def add_strategy(request):
    from .v2_onboarding import wizard
    return wizard(request)
