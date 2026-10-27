"""Authentication events go to the audit log. A logging failure never blocks a sign-in."""
import logging

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from . import audit
from .services import default_membership, personal_org_for

LOG = logging.getLogger(__name__)


def _safely(action, **kwargs):
    try:
        audit.record(action, **kwargs)
    except Exception:  # availability of sign-in beats a missing line; the failure is logged loudly
        LOG.exception("Could not write audit event %s", action)


@receiver(user_logged_in)
def _logged_in(sender, request, user, **kwargs):
    membership = default_membership(user)
    _safely("auth.login", actor=user, request=request,
            organization=membership.organization if membership else None)


@receiver(user_logged_out)
def _logged_out(sender, request, user, **kwargs):
    if user is not None:
        membership = default_membership(user)
        _safely("auth.logout", actor=user, request=request,
                organization=membership.organization if membership else None)


@receiver(user_login_failed)
def _login_failed(sender, credentials, request=None, **kwargs):
    # No username or password is stored: only that a sign-in failed, and from where.
    _safely("auth.login_failed", request=request)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def _new_user_gets_a_personal_organization(sender, instance, created, raw=False, **kwargs):
    # Every account starts with an organization of its own, so no page ever meets a user
    # without one. (Migrations use historical models and are handled by the backfill.)
    if created and not raw:
        personal_org_for(instance)
