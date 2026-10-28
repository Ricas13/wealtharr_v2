"""Account lifecycle: sign-up (off by default), email verification, password reset,
signed-in sessions, data export and account deletion."""
import json
from datetime import datetime, timezone as dt_timezone

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, logout
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordResetForm, UserCreationForm
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.models import Session
from django.core import signing
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from . import audit
from .mail import send_system_email

User = get_user_model()
VERIFY_SALT = "tenancy.verify-email"
VERIFY_MAX_AGE = 2 * 24 * 3600
SIGNUP_WINDOW = 3600
SIGNUP_LIMIT = 5
RESET_LIMIT = 5


def signup_enabled():
    return bool(getattr(settings, "SELF_SERVE_SIGNUP", False))


def _site(request):
    return (settings.SITE_URL or request.build_absolute_uri("/")).rstrip("/")


def _limited(request, name, limit):
    from tracker.client_ip import client_ip
    from tracker.rate_limits import attempts, increment
    key = f"{name}:{client_ip(request) or 'unknown'}"
    if attempts(key) >= limit:
        return True
    increment(key, SIGNUP_WINDOW)
    return False


# ---- sign-up and email verification -------------------------------------------------------

class SignupForm(UserCreationForm):
    email = forms.EmailField()

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")


def _send_verification(request, user):
    token = signing.dumps({"u": user.pk}, salt=VERIFY_SALT)
    link = f"{_site(request)}{reverse('verify_email', args=[token])}"
    return send_system_email(
        user.email, "Confirm your Wealtharr email",
        f"Open this link to confirm your email and activate your account (valid for 2 days):\n\n{link}\n\n"
        "If you did not sign up, ignore this message.")


@require_http_methods(["GET", "POST"])
def signup(request):
    if not signup_enabled():
        raise Http404
    if request.user.is_authenticated:
        return redirect("v2_overview")
    form = SignupForm(request.POST or None)
    if request.method == "POST":
        from tracker.v2_auth import _turnstile_verified
        if _limited(request, "signup", SIGNUP_LIMIT):
            form.add_error(None, "Too many sign-up attempts. Try again later.")
        elif not _turnstile_verified(request):
            form.add_error(None, "Security verification failed. Please try again.")
        elif form.is_valid():
            email = form.cleaned_data["email"].strip()
            if User.objects.filter(email__iexact=email).exists():
                # Same screen as success, so the form cannot be used to find out who has an account.
                return render(request, "tenancy/check_email.html")
            with transaction.atomic():
                user = form.save(commit=False)
                user.email, user.is_active = email, False
                user.save()
                audit.record("account.signup", actor=None, subject=user.pk, target=user, request=request)
            _send_verification(request, user)
            return render(request, "tenancy/check_email.html")
    return render(request, "tenancy/signup.html", {
        "form": form, "turnstile_required": settings.TURNSTILE_REQUIRED,
        "turnstile_site_key": settings.TURNSTILE_SITE_KEY})


@require_http_methods(["GET", "POST"])
def verify_email(request, token):
    # GET only shows a button: mail scanners that open links must not activate accounts.
    try:
        data = signing.loads(token, salt=VERIFY_SALT, max_age=VERIFY_MAX_AGE)
        user = User.objects.get(pk=data["u"])
    except (signing.BadSignature, User.DoesNotExist, KeyError):
        return render(request, "tenancy/verify.html", {"bad": True}, status=400)
    if request.method == "POST":
        if not user.is_active and (user.last_login is not None or not user.has_usable_password()):
            return render(request, "tenancy/verify.html", {"bad": True}, status=400)
        if not user.is_active:
            user.is_active = True
            user.save(update_fields=["is_active"])
            audit.record("account.email_verified", actor=user, subject=user.pk, target=user, request=request)
        messages.success(request, "Email confirmed. You can sign in.")
        return redirect("login")
    return render(request, "tenancy/verify.html", {"token": token})


# ---- password reset -----------------------------------------------------------------------

class ResetForm(PasswordResetForm):
    def send_mail(self, subject_template_name, email_template_name, context, from_email, to_email,
                  html_email_template_name=None):
        from django.template.loader import render_to_string
        subject = "".join(render_to_string(subject_template_name, context).splitlines())
        send_system_email(to_email, subject, render_to_string(email_template_name, context))

    def get_users(self, email):
        # Only people who can sign in; the base class already needs a usable password.
        return (u for u in super().get_users(email) if u.is_active)


# ---- the account page ---------------------------------------------------------------------

def _session_rows(request):
    rows = []
    for s in Session.objects.filter(expire_date__gt=timezone.now()):
        data = s.get_decoded()
        if str(data.get("_auth_user_id")) != str(request.user.pk):
            continue
        rows.append({"key": s.session_key, "ua": data.get("w_ua", "Unknown device")[:120],
                     "ip": data.get("w_ip", ""), "at": data.get("w_at", ""),
                     "current": s.session_key == request.session.session_key})
    return sorted(rows, key=lambda r: r["at"], reverse=True)


def remember_session(sender, request, user, **kwargs):
    """user_logged_in receiver: note the device and time so the owner can recognise sessions."""
    from tracker.client_ip import client_ip
    request.session["w_ua"] = request.META.get("HTTP_USER_AGENT", "")[:200]
    request.session["w_ip"] = client_ip(request) or ""
    request.session["w_at"] = timezone.now().strftime("%Y-%m-%d %H:%M")


@login_required
def account_page(request):
    from tracker.v2_views import _instances
    return render(request, "tenancy/account.html", {
        "shell_instances": _instances(request), "page_key": "account", "page_title": "Account",
        "sessions": _session_rows(request), "can_delete": not request.user.is_staff,
    })


@login_required
@require_POST
def revoke_sessions(request):
    key = request.POST.get("session")
    mine = {r["key"] for r in _session_rows(request)}
    if key == "others":
        targets = mine - {request.session.session_key}
    elif key in mine and key != request.session.session_key:
        targets = {key}
    else:
        targets = set()
    Session.objects.filter(session_key__in=targets).delete()
    if targets:
        audit.record("account.sessions_revoked", actor=request.user, request=request,
                     metadata={"count": len(targets)})
        messages.success(request, "Signed out %d session%s." % (len(targets), "" if len(targets) == 1 else "s"))
    return redirect("account")


# ---- export -------------------------------------------------------------------------------

_SECRET_WORDS = ("ciphertext", "secret", "token", "password", "link_code")


def export_data(user):
    """Everything stored about the user, as plain JSON-able data. Secrets are left out."""
    from django.apps import apps
    out = {"exported_at": timezone.now().isoformat(),
           "user": {"username": user.get_username(), "email": user.email,
                    "joined": user.date_joined.isoformat()},
           "records": {}}
    for model in apps.get_app_config("tracker").get_models():
        names = {f.name for f in model._meta.get_fields() if f.concrete}
        if "owner" in names:
            qs = model._default_manager.filter(owner=user)
        elif "strategy" in names:
            qs = model._default_manager.filter(strategy__owner=user)
        else:
            continue
        fields = [f.name for f in model._meta.concrete_fields
                  if not any(w in f.name for w in _SECRET_WORDS)]
        rows = list(qs.values(*fields))
        if rows:
            out["records"][model.__name__] = rows
    return out


@login_required
@require_POST
def export_download(request):
    audit.record("account.exported", actor=request.user, request=request)
    body = json.dumps(export_data(request.user), cls=DjangoJSONEncoder, indent=2)
    response = HttpResponse(body, content_type="application/json")
    response["Content-Disposition"] = 'attachment; filename="wealtharr-export.json"'
    return response


# ---- deletion -----------------------------------------------------------------------------

def scrub_account(user):
    """Remove everything personal and make the account unusable. Financial history is kept,
    pseudonymous, until the retention policy purges it."""
    from tracker.models import NotificationPreference, OwnerTOTP
    NotificationPreference.objects.filter(owner=user).update(
        destination_email="", email_enabled=False, discord_enabled=False, discord_user_id="",
        discord_username="", discord_webhook_enabled=False, discord_webhook_ciphertext="",
        telegram_enabled=False, telegram_chat_id="", telegram_username="", telegram_link_code="")
    OwnerTOTP.objects.filter(owner=user).delete()
    user.email, user.first_name, user.last_name = "", "", ""
    user.username = f"deleted-{user.pk}"
    user.is_active = False
    user.set_unusable_password()
    user.save()
    for s in Session.objects.all():
        if str(s.get_decoded().get("_auth_user_id")) == str(user.pk):
            s.delete()
    from .models import UserAccess
    if hasattr(UserAccess, "stripe_customer_id"):  # present once billing is installed
        UserAccess.objects.filter(user=user).update(stripe_customer_id="")


@login_required
@require_POST
def delete_account(request):
    user = request.user
    if user.is_staff:
        messages.error(request, "Staff accounts are removed by another administrator.")
        return redirect("account")
    if request.POST.get("confirm", "").strip().lower() != "delete" or not authenticate(
            request, username=user.get_username(), password=request.POST.get("password", "")):
        messages.error(request, "Type DELETE and your password to confirm.")
        return redirect("account")
    from . import gateways
    failed = gateways.cancel_all_for_user(user)  # before the account is scrubbed, while we still know their ids
    with transaction.atomic():
        audit.record("account.deleted", actor=user, subject=user.pk, target=user, request=request,
                     metadata={"subscription_cancel_failed": failed})
        scrub_account(user)
    logout(request)
    messages.success(request, "Your account has been closed and your personal details removed.")
    if failed:
        messages.warning(request, "We could not cancel your subscription with %s. Please cancel it there yourself so you are not charged." % " and ".join(failed))
    return redirect("login")


class RateLimitedResetView(auth_views.PasswordResetView):
    """The usual reset request, limited per address; the answer is the same either way."""
    form_class = ResetForm
    template_name = "tenancy/reset_form.html"
    email_template_name = "tenancy/reset_email.txt"
    subject_template_name = "tenancy/reset_subject.txt"
    success_url = reverse_lazy("password_reset_done")

    def form_valid(self, form):
        if _limited(self.request, "reset", RESET_LIMIT):
            return redirect(self.success_url)
        return super().form_valid(form)
