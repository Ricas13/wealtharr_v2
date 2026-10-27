"""Phase 1: owner-scoped, strategy-neutral v2 page shell.

The financial cockpit under /cockpit/ remains authoritative until later
phases replace its pages. No values, strategy evaluations or trades are
fabricated by these placeholder views.
"""
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render

from .tenant_scope import get_strategy_or_404, strategy_queryset


SECTIONS = ("today", "review", "history", "study", "ledger", "strategy")
LABELS = {
    "today": "Dashboard",
    "review": "Quarterly review",
    "history": "Performance",
    "study": "Study",
    "ledger": "Ledger",
    "strategy": "Strategy & alerts",
}


def _instances(request):
    return list(strategy_queryset(request).order_by("created_at", "pk"))


def _price_freshness(instance):
    """Every required line must pass the same check used by live reviews."""
    from .v2_price_health import strategy_price_health
    return strategy_price_health(instance)


def _render(request, *, page_key, page_title, instance=None, section=None, **extra):
    items = _instances(request)
    if instance is not None and "head_prices" not in extra:
        extra["head_prices"] = _price_freshness(instance)
    if instance is not None:
        from .strategies.instruments import symbols as fund_symbols
        from .strategies.registry import module_for
        from .v2_go_live import setup_missing
        plugin = module_for(instance)
        extra.setdefault("needs_setup", instance.tracking_only or setup_missing(instance))
        extra.setdefault("review_word", plugin.cadence_word if plugin else "quarterly")
        extra.setdefault("weight_label", "Weight at reset"
                         if plugin and plugin.allocation_label == "Reset" else "Target weight")
        extra.setdefault("fund_symbols", fund_symbols(instance))
    from .money import rendering_in, symbol_for
    currency = instance.account.currency if instance is not None else extra.pop("page_currency", "GBP")
    extra.setdefault("cur", symbol_for(currency))
    with rendering_in(currency):
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
    from .v2_overview import overview_page
    return overview_page(request)


@login_required
def instance_page(request, instance_id, section):
    if section not in SECTIONS:
        raise Http404("Unknown strategy page")
    if section == "ledger":
        from .v2_ledger_page import ledger_page
        return ledger_page(request, instance_id)
    if section == "today":
        from .v2_today import today_page
        return today_page(request, instance_id)
    if section == "review":
        from .v2_review import review_page
        return review_page(request, instance_id)
    if section == "study":
        from .v2_study import study_page
        return study_page(request, instance_id)
    if section == "history":
        from .v2_history import history_page
        return history_page(request, instance_id)
    if section == "strategy":
        from .v2_strategy import strategy_page
        return strategy_page(request, instance_id)
    instance = get_strategy_or_404(request, instance_id)
    return _render(
        request, page_key="instance", page_title=LABELS[section],
        instance=instance, section=section,
    )


@login_required
def settings(request):
    from .v2_settings import settings_page
    return settings_page(request)


@login_required
def add_strategy(request):
    from .v2_onboarding import wizard
    return wizard(request)
