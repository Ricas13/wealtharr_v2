"""One place that answers "may this user use this feature?". Fail closed.

Order of precedence: platform admin (always yes) > the user's deny list > the user's grant
list > their access level. Anything that cannot be resolved (unknown feature, no level, no
default level) is a no.
"""
import logging

from . import features
from .models import AccessLevel, UserAccess

LOG = logging.getLogger(__name__)


def _resolve(user):
    """(level, access_row) for the user; the level is None if neither the user nor the default has one."""
    access = UserAccess.objects.select_related("level").filter(user=user).first()
    if access is not None:
        return access.level, access
    return AccessLevel.objects.filter(is_default=True).first(), None


def can(user, key):
    if not getattr(user, "is_authenticated", False):
        return False
    if features.get(key) is None:
        LOG.warning("Access check for undeclared feature %r denied", key)
        return False
    if user.is_superuser:
        return True
    level, access = _resolve(user)
    if access is not None:
        if key in access.deny:
            return False
        if key in access.grant:
            return True
    if level is None:
        return False
    return level.all_features or key in level.features


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
    level, _ = _resolve(user)
    if level is None:
        return 0
    return level.limits.get(key)  # absent in the level: unlimited, as for "Full"
