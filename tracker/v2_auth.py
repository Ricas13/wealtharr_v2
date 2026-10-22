"""Minimal owner authentication retained after retiring the legacy interface."""
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .models import StrategyInstance


def owner_instances(user):
    return StrategyInstance.objects.filter(
        owner=user, account__owner=user
    ).exclude(account__kind="PAPER").order_by("created_at", "pk")


def preferred_url(user, section="today"):
    ids = list(owner_instances(user).values_list("pk", flat=True)[:2])
    if len(ids) == 1:
        return reverse("v2_instance_page", args=[ids[0], section])
    return reverse("v2_overview")


def _turnstile_verified(request):
    if not settings.TURNSTILE_REQUIRED:
        return True
    token = request.POST.get("cf-turnstile-response", "")
    if not settings.TURNSTILE_SECRET_KEY or not settings.TURNSTILE_SITE_KEY or not token:
        return False
    body = urlencode({
        "secret": settings.TURNSTILE_SECRET_KEY,
        "response": token,
    }).encode("ascii")
    try:
        req = Request(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data=body, method="POST",
        )
        with urlopen(req, timeout=8) as result:
            return json.load(result).get("success") is True
    except Exception:
        return False


# Failed sign-ins per username before a 15-minute pause (per web process).
LOGIN_FAILURE_LIMIT = 10
LOGIN_PAUSE_SECONDS = 15 * 60


def _failure_key(request):
    return "login-failures:" + (request.POST.get("username") or "").strip().lower()[:150]


def sign_in(request):
    from django.core.cache import cache
    if request.user.is_authenticated:
        return redirect(preferred_url(request.user))
    form = AuthenticationForm(request=request, data=request.POST or None)
    error = ""
    if request.method == "POST":
        key = _failure_key(request)
        # An unbound form: a bound one would run the password check when rendered.
        retry = {"username": (request.POST.get("username") or "")[:150]}
        # Checks run before the password, so a failed check never reveals whether it was right.
        if cache.get(key, 0) >= LOGIN_FAILURE_LIMIT:
            form = AuthenticationForm(request=request, initial=retry)
            error = "Too many failed sign-ins. Try again in 15 minutes."
        elif not _turnstile_verified(request):
            form = AuthenticationForm(request=request, initial=retry)
            error = "Security verification failed. Please try again."
        elif form.is_valid():
            cache.delete(key)
            login(request, form.get_user())
            return redirect(preferred_url(request.user))
        else:
            cache.set(key, cache.get(key, 0) + 1, LOGIN_PAUSE_SECONDS)
    return render(request, "v2/login.html", {
        "form": form, "login_error": error, "turnstile_site_key": settings.TURNSTILE_SITE_KEY,
        "turnstile_required": settings.TURNSTILE_REQUIRED,
    })


@login_required
@require_POST
def sign_out(request):
    logout(request)
    return redirect("login")


@login_required
def change_password(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "Password changed.")
        return redirect("v2_settings")
    return render(request, "v2/password.html", {"form": form})


@staff_member_required
def control(request):
    """Owner admin page, inside the normal app shell (staff only)."""
    from .forms import IntegrationForm
    from .discord_dm import bot_invite_url, configured as discord_ready, redirect_uri
    from .models import (InviteRequest, NotificationPreference, NotificationSchedulerRun, PriceRevision,
                         SystemIntegration, YahooSync)
    from .v2_views import _render
    cfg = SystemIntegration.current()
    form = IntegrationForm(request.POST or None, instance=cfg)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Mail and Discord settings saved.")
        return redirect("control")
    return _render(
        request, page_key="admin", page_title="Admin",
        form=form,
        smtp_password_set=bool(cfg.smtp_password_ciphertext),
        discord_set=discord_ready(cfg),
        discord_redirect=redirect_uri(request),
        discord_bot_invite=bot_invite_url(cfg),
        discord_connected=NotificationPreference.objects.exclude(discord_user_id="").count(),
        turnstile_set=bool(settings.TURNSTILE_SITE_KEY and settings.TURNSTILE_SECRET_KEY),
        turnstile_required=settings.TURNSTILE_REQUIRED,
        scheduler_run=NotificationSchedulerRun.objects.order_by("-started_at", "-pk").first(),
        invite_count=InviteRequest.objects.count(),
        price_problems=YahooSync.objects.exclude(last_error="").order_by("symbol"),
        price_revisions=PriceRevision.objects.filter(accepted_at__isnull=True),
    )


@staff_member_required
@require_POST
def accept_price_revision(request, symbol):
    """Accept Yahoo's close for every pending revision of one ticker and release its hold."""
    from django.db import transaction
    from .market_data import apply_revision
    from .models import PriceRevision, YahooSync
    with transaction.atomic():
        pending = list(PriceRevision.objects.select_for_update().filter(symbol=symbol, accepted_at__isnull=True))
        if not pending:
            messages.error(request, "Nothing is waiting for %s." % symbol)
            return redirect("control")
        for revision in pending:
            if revision.accepted_at is None:
                apply_revision(revision, request.user)
        # Check again on the next hourly run with the corrected history.
        YahooSync.objects.filter(symbol=symbol, last_error="PRICE_HOLD:REVISED_HISTORICAL_CLOSE").update(
            last_error="", last_attempt=None)
    messages.success(request, "Accepted Yahoo's close for %s. Prices refresh on the next hourly check." % symbol)
    return redirect("control")


@require_GET
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return JsonResponse({"status": "ok"})
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
