"""Owner strategy identity, released rules and actual baseline evidence.

This page never changes strategy identity, prices, executions or rules; the
only thing edited here is the planned monthly top-up used by reminders.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .core.catalogue import rows_for, MappingNotAvailable
from .models import StrategyGoLive, StrategyInstance
from .v2_go_live import attestation_text, first_entry_date
from .strategies.registry import describe_strategy
from .v2_views import _render


@login_required
@require_GET
def strategy_page(request, instance_id):
    instance = get_object_or_404(
        StrategyInstance.objects.select_related("account", "preset")
        .filter(owner=request.user, account__owner=request.user)
        .exclude(account__kind="PAPER"), pk=instance_id,
    )
    key = instance.strategy_key or instance.preset.slug
    description = describe_strategy(key)
    try:
        lines = rows_for(
            key, instance.account.broker, instance.account.kind,
            country=instance.account.country,
        )
    except MappingNotAvailable:
        lines = ()
    events = []
    if instance.strategy_version or instance.mapping_version:
        events.append({
            "title": "Pinned at enrolment",
            "date": instance.started_on or instance.created_at.date(),
            "strategy": instance.strategy_version or "Not pinned",
            "mapping": instance.mapping_version or "Not pinned",
        })
    live = StrategyGoLive.objects.filter(strategy=instance).first()
    if live:
        events.insert(0, {
            "title": "Owner confirmed go-live",
            "date": live.confirmed_at.date(),
            "strategy": live.strategy_version, "mapping": live.mapping_version,
        })
    for row in instance.baselines.order_by("-as_of", "-pk")[:12]:
        events.append({
            "title": "Owner-confirmed baseline",
            "date": row.as_of,
            "strategy": row.strategy_version or "Not recorded",
            "mapping": row.mapping_version or "Not recorded",
        })
    from .v2_dashboard import alerts_summary
    from .v2_settings import strategy_route_rows
    from .models import NotificationPreference
    last_by_category = {}
    for receipt in instance.notification_receipts.order_by("-created_at", "-pk")[:50]:
        last_by_category.setdefault(receipt.category, receipt.created_at)
    routes = strategy_route_rows(instance)
    for row in routes:
        row["last"] = last_by_category.get(row["key"])
    return _render(
        request, page_key="instance", page_title="Strategy & alerts",
        strategy_routes=routes, strategy_alerts=alerts_summary(request.user, instance),
        strategy_preference=NotificationPreference.objects.filter(owner=request.user).first(),
        instance=instance, section="strategy",
        strategy_description=description, strategy_catalogue_rows=lines,
        strategy_history=tuple(events),
        strategy_tracking_only=instance.tracking_only,
        strategy_go_live=live,
        strategy_attestation=attestation_text(instance),
        strategy_default_start=first_entry_date(instance) or timezone.localdate(),
    )


@login_required
@require_POST
def plan_view(request, instance_id):
    """Change the planned monthly top-up. Only reminders use it; history is untouched."""
    from django.contrib import messages
    from django.core.exceptions import ValidationError
    from django.shortcuts import redirect
    from django.urls import reverse
    from .v2_onboarding import _decimal
    instance = get_object_or_404(
        StrategyInstance.objects.filter(owner=request.user, account__owner=request.user)
        .exclude(account__kind="PAPER"), pk=instance_id)
    try:
        amount = _decimal(request.POST.get("monthly_target", ""), "Monthly plan")
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    else:
        StrategyInstance.objects.filter(pk=instance.pk).update(monthly_target=amount)
        messages.success(request, "Monthly plan saved." if amount else "Monthly reminder turned off.")
    return redirect(reverse("v2_instance_page", args=[instance.pk, "strategy"]) + "#monthly-plan")
