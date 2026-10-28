"""Consent-based, time-boxed, audited support access.

The platform admin sees **metadata only** (counts, plan, which alerts are on, when things
happened) and only for users who currently allow it. No amounts, prices, ledger rows or
messages are shown. Every look is written to the user's own audit trail, which the user can read.
"""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from . import audit
from .models import AuditEvent, SupportGrant, UserAccess

User = get_user_model()
DURATIONS = {"1": 1, "4": 4, "24": 24, "72": 72}  # hours offered to the user
platform_admin = user_passes_test(lambda u: u.is_active and u.is_superuser, login_url="/login/")


def active_grant(user):
    return next((g for g in SupportGrant.objects.filter(user=user, revoked_at__isnull=True,
                                                        expires_at__gt=timezone.now())), None)


# ---- the user's side ----------------------------------------------------------------------

@login_required
@require_http_methods(["GET", "POST"])
def my_support_access(request):
    if request.method == "POST":
        hours = DURATIONS.get(request.POST.get("hours", ""))
        if hours is None:
            messages.error(request, "Choose how long to allow access.")
        else:
            grant = SupportGrant.objects.create(
                user=request.user, reason=request.POST.get("reason", "")[:200].strip(),
                expires_at=timezone.now() + timedelta(hours=hours))
            audit.record("support.granted", actor=request.user, target=grant, request=request,
                         metadata={"hours": hours})
            messages.success(request, "Support access is on until %s." % grant.expires_at.strftime("%d %b %H:%M"))
        return redirect("support_access")
    from tracker.v2_views import _instances
    log = AuditEvent.objects.filter(subject_id=request.user.pk, action__startswith="support.").order_by("-id")[:50]
    return render(request, "tenancy/support_access.html", {
        "shell_instances": _instances(request), "page_key": "support", "page_title": "Support access",
        "grant": active_grant(request.user), "log": log})


@login_required
@require_POST
def revoke_support_access(request):
    grants = SupportGrant.objects.filter(user=request.user, revoked_at__isnull=True, expires_at__gt=timezone.now())
    for grant in grants:
        grant.revoked_at = timezone.now()
        grant.save(update_fields=["revoked_at"])
        audit.record("support.revoked", actor=request.user, target=grant, request=request)
    messages.success(request, "Support access is off.")
    return redirect("support_access")


# ---- the platform admin's side ------------------------------------------------------------

@platform_admin
def support_index(request):
    from tracker.v2_views import _instances
    grants = SupportGrant.objects.filter(revoked_at__isnull=True, expires_at__gt=timezone.now()).select_related("user")
    return render(request, "tenancy/support_index.html", {
        "shell_instances": _instances(request), "page_key": "support_admin", "page_title": "Support",
        "grants": grants})


def account_metadata(user):
    """The only facts a support look may show. Counts and flags, never values."""
    from tracker.models import (Account, LedgerEntry, NotificationDelivery, NotificationPreference,
                                StrategyInstance, StrategySignal)
    access = UserAccess.objects.select_related("plan").filter(user=user).first()
    pref = NotificationPreference.objects.filter(owner=user).first()
    since = timezone.now() - timedelta(days=30)
    deliveries = {}
    for status in NotificationDelivery.objects.filter(owner=user, created_at__gte=since).values_list("status", flat=True):
        deliveries[status] = deliveries.get(status, 0) + 1
    return {
        "joined": user.date_joined, "last_login": user.last_login, "active": user.is_active,
        "plan": access.plan.name if access else "(default plan)",
        "accounts": Account.objects.filter(owner=user).count(),
        "strategies": list(StrategyInstance.objects.filter(owner=user).values_list("preset__slug", flat=True)),
        "ledger_entries": LedgerEntry.objects.filter(strategy__owner=user).count(),
        "reviews": StrategySignal.objects.filter(strategy__owner=user).count(),
        "channels": {name: bool(pref and getattr(pref, name + "_enabled", False))
                     for name in ("email", "discord", "telegram")},
        "deliveries_30d": deliveries,
        "recent_events": list(AuditEvent.objects.filter(subject_id=user.pk).exclude(action__startswith="support.")
                              .order_by("-id").values_list("occurred_at", "action")[:20]),
    }


@platform_admin
def support_view(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    grant = active_grant(user)
    if grant is None:
        raise Http404  # no consent, or it ran out: the page does not exist for the admin
    audit.record("support.viewed", actor=request.user, subject=user.pk, target=grant, request=request)
    from tracker.v2_views import _instances
    return render(request, "tenancy/support_view.html", {
        "shell_instances": _instances(request), "page_key": "support_admin", "page_title": "Support view",
        "subject": user, "grant": grant, "meta": account_metadata(user)})
