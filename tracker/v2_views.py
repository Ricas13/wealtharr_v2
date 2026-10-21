"""Phase 1: owner-scoped, strategy-neutral v2 page shell.

The financial cockpit under /cockpit/ remains authoritative until later
phases replace its pages. No values, strategy evaluations or trades are
fabricated by these placeholder views.
"""
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from .models import StrategyInstance


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
    return list(
        StrategyInstance.objects.filter(
            owner=request.user, account__owner=request.user
        ).exclude(account__kind="PAPER").select_related(
            "account", "preset"
        ).order_by("created_at", "pk")
    )


def _price_freshness(instance):
    """Latest stored close for the strategy's instruments, for the header chip."""
    from datetime import timedelta
    from django.utils import timezone
    from .models import MarketObservation, YahooSync
    from .strategies.instruments import symbols as fund_symbols
    symbols = list(fund_symbols(instance))
    latest = MarketObservation.objects.filter(
        symbol__in=symbols, currency="GBP").order_by("-as_of").first()
    sync = YahooSync.objects.filter(symbol__in=symbols).order_by("-last_attempt").first()
    return {
        "date": latest.as_of if latest else None,
        "fresh": bool(latest and latest.as_of >= timezone.localdate() - timedelta(days=4)),
        "checked": sync.last_attempt if sync else None,
    }


def _render(request, *, page_key, page_title, instance=None, section=None, **extra):
    items = _instances(request)
    if instance is not None and "head_prices" not in extra:
        extra["head_prices"] = _price_freshness(instance)
    if instance is not None:
        from .v2_go_live import setup_missing
        extra.setdefault("needs_setup", instance.tracking_only or setup_missing(instance))
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
    from .v2_history import overview_page
    return overview_page(request)


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
    if section == "review":
        from .v2_today import review_page
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
def settings(request):
    from .v2_settings import settings_page
    return settings_page(request)


@login_required
def add_strategy(request):
    from .v2_onboarding import wizard
    return wizard(request)
