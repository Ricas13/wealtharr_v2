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


def sign_in(request):
    if request.user.is_authenticated:
        return redirect(preferred_url(request.user))
    form = AuthenticationForm(request=request, data=request.POST or None)
    if request.method == "POST":
        valid = form.is_valid()
        verification_ok = _turnstile_verified(request)
        if valid and verification_ok:
            login(request, form.get_user())
            return redirect(preferred_url(request.user))
        if not verification_ok:
            form.add_error(None, "Security verification failed. Please try again.")
    return render(request, "v2/login.html", {
        "form": form, "turnstile_site_key": settings.TURNSTILE_SITE_KEY,
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
    from .models import InviteRequest, NotificationSchedulerRun, PriceRevision, SystemIntegration, YahooSync
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
        discord_set=bool(cfg.discord_url_ciphertext),
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
    from django.utils import timezone
    from .models import MarketObservation, PriceRevision, YahooSync
    with transaction.atomic():
        pending = list(PriceRevision.objects.select_for_update().filter(symbol=symbol, accepted_at__isnull=True))
        if not pending:
            messages.error(request, "Nothing is waiting for %s." % symbol)
            return redirect("control")
        for revision in pending:
            observation = MarketObservation.objects.select_for_update().filter(
                symbol=symbol, as_of=revision.as_of).first()
            if observation is not None and observation.price == revision.stored_price:
                observation.price = revision.yahoo_price
                if observation.raw_unit_verified:
                    factor = 100 if observation.raw_quote_unit in ("GBp", "GBX") else 1
                    observation.raw_close = revision.yahoo_raw_close or revision.yahoo_price * factor
                observation.save(update_fields=["price", "raw_close"])
            revision.accepted_at, revision.accepted_by = timezone.now(), request.user
            revision.save(update_fields=["accepted_at", "accepted_by"])
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
