"""The one place a request's strategies are looked up.

Every page that needs "the signed-in user's strategy" goes through here, so the rule is
written once: through the tenant manager (the signed-in user's rows; fail closed with nobody
active) and, explicitly, owned by that user and their account.
"""
from django.shortcuts import get_object_or_404

from tenancy.context import tenant_context

from .models import Account, StrategyInstance


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


def strategies_of(user, *, include_paper=True):
    """A user's strategies, for code that has a user but no request (services, jobs, forms).

    Still read through the tenant manager: the context is set for the moment the query is built.
    """
    with tenant_context(user):
        rows = StrategyInstance.tenant_objects.filter(owner=user, account__owner=user)
    return rows if include_paper else rows.exclude(account__kind="PAPER")


def accounts_of(user):
    """A user's accounts, through the tenant manager."""
    with tenant_context(user):
        return Account.tenant_objects.filter(owner=user)
