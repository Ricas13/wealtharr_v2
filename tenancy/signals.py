"""Authentication events go to the audit log. A logging failure never blocks a sign-in."""
import logging

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from . import audit

LOG = logging.getLogger(__name__)


def _safely(action, **kwargs):
    try:
        audit.record(action, **kwargs)
    except Exception:  # availability of sign-in beats a missing line; the failure is logged loudly
        LOG.exception("Could not write audit event %s", action)


@receiver(user_logged_in)
def _logged_in(sender, request, user, **kwargs):
    _safely("auth.login", actor=user, request=request)


@receiver(user_logged_out)
def _logged_out(sender, request, user, **kwargs):
    if user is not None:
        _safely("auth.logout", actor=user, request=request)


@receiver(user_login_failed)
def _login_failed(sender, credentials, request=None, **kwargs):
    # No username or password is stored: only that a sign-in failed, and from where.
    _safely("auth.login_failed", request=request)



user_logged_in.connect(
    __import__("tenancy.accounts", fromlist=["remember_session"]).remember_session,
    dispatch_uid="tenancy.remember_session")
