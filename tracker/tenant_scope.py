"""The one place a request's strategies are looked up.

Every page that needs "the signed-in user's strategy" goes through here, so the rule is
written once: through the tenant manager (the signed-in user's rows; fail closed with nobody
active) and, explicitly, owned by that user and their account.
"""
from django.shortcuts import get_object_or_404

from .models import StrategyInstance


def strategy_queryset(request, *, include_paper=False):
    """Strategies the request may see: this user's own, no paper accounts."""
    rows = StrategyInstance.tenant_objects.filter(
        owner=request.user, account__owner=request.user,
    ).select_related("account", "preset")
    return rows if include_paper else rows.exclude(account__kind="PAPER")


def get_strategy_or_404(request, instance_id, *, locked=False):
    """One strategy by id, or a 404 that is the same for "missing" and "someone else's"."""
    rows = strategy_queryset(request)
    if locked:
        rows = rows.select_for_update()
    return get_object_or_404(rows, pk=instance_id)
