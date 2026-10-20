"""Phase 7: owner-only Settings and evidence-aware notification diagnostics.

Notification secrets remain in existing staff-only IntegrationForm; this
view never renders them, never accesses a broker and never certifies backups.
"""
from uuid import uuid4
from datetime import timedelta

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .forms import NotificationForm
from .models import (
    NotificationDelivery, NotificationPreference, NotificationSchedulerRun,
    OwnerTOTP, StrategyInstance, SystemIntegration,
)
from .notification_routing import CATEGORIES, CHANNELS
from .services import dispatch
from .v2_views import _render, _instances

# The onboarding UI calls outstanding review follow-ups "unacted". Preserve
# this existing persisted identifier instead of inventing a second route.
_INSTANCE_CATEGORY = {
    "quarterly": "quarterly",
    "urgent": "urgent",
    "data": "data",
    "followup": "unacted",
    "contribution": "contribution",
}


def strategy_route_rows(strategy):
    routes = strategy.alert_routes if isinstance(strategy.alert_routes, dict) else {}
    category_rows = []
    for category, title in CATEGORIES:
        key = _INSTANCE_CATEGORY[category]
        explicit = routes.get(key)
        # Existing legacy portfolios without per-strategy preferences
        # inherit global choices until the owner edits this table.
        inherited = not routes and not strategy.strategy_key
        category_rows.append({
            "key": key,
            "title": title,
            "when": CATEGORY_WHEN.get(key, ""),
            "email": (
                explicit.get("email") is True if isinstance(explicit, dict)
                else inherited
            ),
            "discord": (
                explicit.get("discord") is True if isinstance(explicit, dict)
                else inherited
            ),
        })
    return category_rows


CATEGORY_WHEN = {
    "quarterly": "After a quarter end, when trades are needed",
    "urgent": "Big market drop or a reset to review",
    "data": "Yahoo prices stale or missing",
    "unacted": "A review is still not done",
    "contribution": "Monthly deposit reminder",
}


@login_required
@require_http_methods(["GET", "POST"])
def settings_page(request):
    pref, _ = NotificationPreference.objects.get_or_create(
        owner=request.user, defaults={"destination_email": request.user.email}
    )
    error = ""
    kind = request.POST.get("form_kind") if request.method == "POST" else ""
    form = NotificationForm(
        request.POST if kind == "owner" else None, instance=pref
    )
    if request.method == "POST":
        if kind == "owner":
            if form.is_valid():
                if (form.cleaned_data["email_enabled"] and
                        not form.cleaned_data["destination_email"]):
                    form.add_error("destination_email", "An email address is required when email is enabled.")
                else:
                    form.save()
                    messages.success(request, "Alert destinations saved.")
                    return redirect("v2_settings")
        elif kind == "strategy":
            try:
                strategy_id = int(request.POST.get("strategy_id", ""))
            except (TypeError, ValueError):
                raise Http404("Unknown strategy")
            strategy = get_object_or_404(
                StrategyInstance.objects.select_related("account"),
                owner=request.user, account__owner=request.user,
                pk=strategy_id,
            )
            if strategy.account.kind == "PAPER":
                raise Http404("Unsupported account type")
            # Explicit full, fixed-size choices. No user-provided category,
            # ticker, webhook or strategy identifier is stored as a new key.
            strategy.alert_routes = {
                key: {
                    channel: request.POST.get(
                        "route_%s_%s" % (key, channel)
                    ) == "on" for channel in CHANNELS
                }
                for key in _INSTANCE_CATEGORY.values()
            }
            strategy.save(update_fields=["alert_routes"])
            messages.success(request, "Alert routing updated for " + strategy.name + ".")
            if request.POST.get("return_to") == "strategy":
                return redirect("v2_instance_page", instance_id=strategy.pk, section="strategy")
            return redirect("v2_settings")
        else:
            error = "Unsupported settings action. No preferences were changed."

    settings_instances = _instances(request)
    route_rows = [{"instance": strategy, "rows": strategy_route_rows(strategy)}
                  for strategy in settings_instances]
    config = SystemIntegration.objects.filter(pk=1).first()
    last_receipts = list(
        NotificationDelivery.objects.filter(owner=request.user)
        .order_by("-created_at", "-pk")
        .values("channel", "category", "status", "created_at", "attempts")[:10]
    )
    latest_run = (
        NotificationSchedulerRun.objects.order_by("-started_at", "-pk").first()
        if request.user.is_staff else None
    )
    scheduler_recent = bool(
        latest_run and latest_run.status == "SUCCEEDED" and
        latest_run.finished_at and
        latest_run.finished_at >= timezone.now() - timedelta(hours=3)
    )
    return _render(
        request, page_key="settings", page_title="Settings",
        notification_form=form, settings_error=error,
        category_rows=CATEGORIES, strategy_routes=route_rows,
        preference=pref, last_receipts=last_receipts,
        smtp_configured=bool(
            config and config.smtp_host and config.smtp_sender
        ),
        discord_configured=bool(config and config.discord_url_ciphertext),
        has_authenticator=OwnerTOTP.objects.filter(owner=request.user).exists(),
        scheduler_run=latest_run, scheduler_recent=scheduler_recent,
    )


@staff_member_required
@require_POST
def test_channel(request, channel):
    if channel not in CHANNELS:
        raise Http404("Unknown notification channel")
    pref, _ = NotificationPreference.objects.get_or_create(owner=request.user)
    if not getattr(pref, channel + "_enabled"):
        messages.error(request, "Enable the destination before sending a test.")
        return redirect("v2_settings")
    # Real attempt, with a unique idempotency key. Success only means a
    # transport acknowledgment; it does not certify future delivery.
    outcome = dispatch(
        request.user, "settings-test:" + uuid4().hex,
        "Wealtharr channel test",
        "This is a notification transport test. No broker action occurred.",
        channels=(channel,),
    )
    if outcome.get(channel) == "sent":
        messages.success(request, channel.title() + " test accepted by the transport.")
    else:
        messages.error(request, channel.title() + " test was not confirmed; check the integration and receipts.")
    return redirect("v2_settings")
