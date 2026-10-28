"""Phase 7: owner-only Settings and evidence-aware notification diagnostics.

Notification secrets remain in existing staff-only IntegrationForm; this
view never renders them, never accesses a broker and never certifies backups.
"""
from uuid import uuid4

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from .forms import NotificationForm
from tenancy import access

from .tenant_scope import get_strategy_or_404
from .models import (
    NotificationDelivery, NotificationPreference,
    OwnerTOTP, StrategyInstance, SystemIntegration,
)
from .notification_routing import CATEGORIES, CHANNELS, DELIVERY_CHANNELS
from .discord_dm import DiscordError, authorize_url, configured as discord_ready, connect, redirect_uri
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
            "telegram": isinstance(explicit, dict) and explicit.get("telegram") is True,
        })
    return category_rows


CHANNEL_NAMES = {"email": "Email", "discord": "Discord DM", "discord_webhook": "Discord channel webhook",
                 "telegram": "Telegram"}
TELEGRAM_LINK_MINUTES = 15
CATEGORY_WHEN = {
    "quarterly": "After a quarter end, when trades are needed",
    "urgent": "Big market drop or a reset to review",
    "data": "Yahoo prices stale or missing",
    "unacted": "A review is still not done",
    "contribution": "Missing monthly deposits or newly available money to invest",
}


# Which plan feature each switch on the alerts form needs.
CHANNEL_FIELD_FEATURE = {
    "email_enabled": "alerts.email", "discord_enabled": "alerts.discord",
    "discord_webhook_enabled": "alerts.discord", "telegram_enabled": "alerts.telegram",
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
                for field, feature in CHANNEL_FIELD_FEATURE.items():
                    if form.cleaned_data[field] and not access.can(request.user, feature):
                        form.add_error(field, "Your plan does not include this alert channel.")
            if form.is_valid():
                if (form.cleaned_data["email_enabled"] and
                        not form.cleaned_data["destination_email"]):
                    form.add_error("destination_email", "An email address is required when email is enabled.")
                else:
                    form.save()
                    messages.success(request, "Alert destinations saved.")
                    return redirect("v2_settings")
        elif kind in ("telegram_start", "telegram_finish", "telegram_disconnect"):
            _telegram_step(request, pref, kind)
            return redirect(reverse("v2_settings") + "#telegram")
        elif kind == "remove_webhook":
            pref.discord_webhook_ciphertext = ""
            pref.discord_webhook_enabled = False
            pref.save(update_fields=["discord_webhook_ciphertext", "discord_webhook_enabled"])
            messages.success(request, "Discord webhook removed. Nothing more will be posted to that channel.")
            return redirect("v2_settings")
        elif kind == "strategy":
            try:
                strategy_id = int(request.POST.get("strategy_id", ""))
            except (TypeError, ValueError):
                raise Http404("Unknown strategy")
            strategy = get_strategy_or_404(request, strategy_id)
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
    return _render(
        request, page_key="settings", page_title="Settings",
        notification_form=form, settings_error=error,
        category_rows=CATEGORIES, strategy_routes=route_rows,
        preference=pref, last_receipts=last_receipts,
        smtp_configured=bool(
            config and config.smtp_host and config.smtp_sender
        ),
        discord_configured=bool(config and discord_ready(config)),
        telegram_configured=bool(config and config.telegram_bot_username),
        telegram_link=_telegram_link(pref, config),
        has_authenticator=OwnerTOTP.objects.filter(owner=request.user).exists(),
        plan_blocked_channels=[label for label, feature in (
            ("Email", "alerts.email"), ("Discord", "alerts.discord"), ("Telegram", "alerts.telegram"))
            if not access.can(request.user, feature)],
    )


def _telegram_link(pref, config):
    """The "open the bot" link while a connection is waiting, else ""."""
    from django.utils import timezone
    from .telegram_bot import start_link
    if (config and config.telegram_bot_username and pref.telegram_link_code and
            pref.telegram_link_expires and pref.telegram_link_expires > timezone.now()):
        return start_link(config.telegram_bot_username, pref.telegram_link_code)
    return ""


def _copy_discord_routes_to_telegram(owner):
    """On first connect, strategies send to Telegram what they already send to Discord."""
    for strategy in StrategyInstance.objects.filter(owner=owner):
        routes = strategy.alert_routes if isinstance(strategy.alert_routes, dict) else {}
        changed = False
        for row in routes.values():
            if isinstance(row, dict) and "telegram" not in row:
                row["telegram"] = row.get("discord") is True
                changed = True
        if changed:
            strategy.alert_routes = routes
            strategy.save(update_fields=["alert_routes"])


def _telegram_step(request, pref, kind):
    from datetime import timedelta
    from secrets import token_urlsafe
    from django.utils import timezone
    from .services import decrypt_secret
    from .telegram_bot import TelegramError, find_start
    fields = ["telegram_link_code", "telegram_link_expires"]
    if kind == "telegram_disconnect":
        pref.telegram_chat_id = pref.telegram_username = pref.telegram_link_code = ""
        pref.telegram_connected_at = pref.telegram_link_expires = None
        pref.telegram_enabled = False
        pref.save(update_fields=fields + ["telegram_chat_id", "telegram_username",
                                          "telegram_connected_at", "telegram_enabled"])
        messages.success(request, "Telegram disconnected. No more Telegram messages will be sent.")
        return
    cfg = SystemIntegration.current()
    if not cfg.telegram_bot_username:
        messages.error(request, "Telegram isn't set up by the site owner yet.")
        return
    if kind == "telegram_start":
        pref.telegram_link_code = token_urlsafe(24)
        pref.telegram_link_expires = timezone.now() + timedelta(minutes=TELEGRAM_LINK_MINUTES)
        pref.save(update_fields=fields)
        return
    if not _telegram_link(pref, cfg):
        messages.error(request, "That Telegram link expired. Click Connect Telegram again.")
        return
    try:
        found = find_start(decrypt_secret(cfg.telegram_bot_token_ciphertext), pref.telegram_link_code)
    except TelegramError as exc:
        messages.error(request, "Telegram wasn't connected: %s" % exc)
        return
    if not found:
        messages.error(request, "Telegram hasn't seen you press Start yet. Open the link, press Start, then try again.")
        return
    first = not pref.telegram_connected_at
    pref.telegram_chat_id, pref.telegram_username = found
    pref.telegram_connected_at = timezone.now()
    pref.telegram_enabled = True
    pref.telegram_link_code, pref.telegram_link_expires = "", None
    pref.save(update_fields=fields + ["telegram_chat_id", "telegram_username",
                                      "telegram_connected_at", "telegram_enabled"])
    if first:
        _copy_discord_routes_to_telegram(request.user)
    messages.success(request, "Telegram connected. Alerts will arrive from the Wealtharr bot.")


@staff_member_required
@require_POST
def test_channel(request, channel):
    if channel not in DELIVERY_CHANNELS:
        raise Http404("Unknown notification channel")
    pref, _ = NotificationPreference.objects.get_or_create(owner=request.user)
    if not getattr(pref, channel + "_enabled"):
        messages.error(request, "Turn on that destination in Alerts & settings before sending a test.")
        return redirect("control")
    # Real attempt, with a unique idempotency key. Success only means a
    # transport acknowledgment; it does not certify future delivery.
    outcome = dispatch(
        request.user, "settings-test:" + uuid4().hex,
        "Wealtharr channel test",
        "This is a notification transport test. No broker action occurred.",
        channels=(channel,),
    )
    if outcome.get(channel) == "sent":
        messages.success(request, CHANNEL_NAMES[channel] + " test accepted by the transport.")
    else:
        messages.error(request, CHANNEL_NAMES[channel] + " test was not confirmed; check the integration and receipts.")
    return redirect("control")


@login_required
@require_http_methods(["GET"])
def discord_connect(request):
    """Send the person to Discord's own consent screen."""
    from secrets import token_urlsafe
    cfg = SystemIntegration.current()
    if not discord_ready(cfg):
        messages.error(request, "Discord DMs aren't set up by the site owner yet.")
        return redirect("v2_settings")
    state = token_urlsafe(24)
    request.session["discord_state"] = state
    return redirect(authorize_url(cfg, redirect_uri(request), state))


@login_required
@require_http_methods(["GET"])
def discord_callback(request):
    """Back from Discord: link the account, join the bot's server, turn DMs on."""
    from django.utils import timezone
    expected = request.session.pop("discord_state", None)
    if not expected or request.GET.get("state") != expected:
        messages.error(request, "That Discord link expired. Try Connect Discord again.")
        return redirect("v2_settings")
    if request.GET.get("error") or not request.GET.get("code"):
        messages.error(request, "Discord wasn't connected.")
        return redirect("v2_settings")
    cfg = SystemIntegration.current()
    try:
        user_id, name = connect(cfg, request.GET["code"], redirect_uri(request))
    except DiscordError as exc:
        messages.error(request, "Discord wasn't connected: %s" % exc)
        return redirect("v2_settings")
    pref, _ = NotificationPreference.objects.get_or_create(
        owner=request.user, defaults={"destination_email": request.user.email})
    pref.discord_user_id, pref.discord_username = user_id, name
    pref.discord_connected_at = timezone.now()
    pref.discord_enabled = True
    pref.save(update_fields=["discord_user_id", "discord_username", "discord_connected_at", "discord_enabled"])
    messages.success(request, "Discord connected. Alerts will arrive as DMs from the Wealtharr bot.")
    return redirect("v2_settings")


@login_required
@require_POST
def discord_disconnect(request):
    pref, _ = NotificationPreference.objects.get_or_create(owner=request.user)
    pref.discord_user_id = pref.discord_username = ""
    pref.discord_connected_at = None
    pref.discord_enabled = False
    pref.save(update_fields=["discord_user_id", "discord_username", "discord_connected_at", "discord_enabled"])
    messages.success(request, "Discord disconnected. No more DMs will be sent.")
    return redirect("v2_settings")
