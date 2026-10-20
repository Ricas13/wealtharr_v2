"""Read-only owner strategy identity, released rules and actual baseline evidence.

Notification editing remains centralized in Phase 7 owner Settings. This page
never changes strategy identity, prices, executions, rules or alert routing.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from .core.catalogue import rows_for, MappingNotAvailable
from .models import StrategyGoLive, StrategyInstance
from .v2_go_live import attestation_text
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
    return _render(
        request, page_key="instance", page_title="Strategy",
        instance=instance, section="strategy",
        strategy_description=description, strategy_catalogue_rows=lines,
        strategy_history=tuple(events),
        strategy_tracking_only=instance.tracking_only,
        strategy_go_live=live,
        strategy_attestation=attestation_text(instance),
    )
