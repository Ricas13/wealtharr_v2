"""One place that answers "may this user use this feature?". Fail closed.

Order of precedence: platform admin (always yes) > the user's deny list > the user's grant
list > their plan. Anything that cannot be resolved (unknown feature, no plan, no
default plan) is a no.
"""
import logging

from . import features
from .models import Plan, UserAccess

LOG = logging.getLogger(__name__)


def _resolve(user):
    """(plan, access_row) for the user; the plan is None if neither the user nor the default has one."""
    access = UserAccess.objects.select_related("plan").filter(user=user).first()
    if access is not None:
        return access.plan, access
    return Plan.objects.filter(is_default=True).first(), None


def can(user, key):
    if not getattr(user, "is_authenticated", False):
        return False
    if features.get(key) is None:
        LOG.warning("Access check for undeclared feature %r denied", key)
        return False
    if user.is_superuser:
        return True
    plan, access = _resolve(user)
    if access is not None:
        if key in access.deny:
            return False
        if key in access.grant:
            return True
    if plan is None:
        return False
    return plan.all_features or key in plan.features


def limit(user, key):
    """The numeric limit for a feature: None means unlimited, 0 when it cannot be resolved."""
    if not getattr(user, "is_authenticated", False):
        return 0
    feature = features.get(key)
    if feature is None or not feature.limit:
        LOG.warning("Limit check for undeclared or non-numeric feature %r denied", key)
        return 0
    if user.is_superuser:
        return None
    plan, _ = _resolve(user)
    if plan is None:
        return 0
    return plan.limits.get(key)  # absent in the plan: unlimited, as for "Full"
