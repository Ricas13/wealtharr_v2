"""Owner-authenticated setup and second-step challenge for optional TOTP."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import never_cache
from django.utils import timezone

from .models import OwnerTOTP
from .services import decrypt_secret, encrypt_secret
from .totp import generate_secret, matching_step, provisioning_uri, verify_account_code

SESSION_FLAG = "wealtharr_totp_verified_uid"
ENROLL_SECRET = "wealtharr_pending_totp"


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def challenge(request):
    if not OwnerTOTP.objects.filter(owner=request.user).exists():
        return redirect("cockpit_today")
    if request.session.get(SESSION_FLAG) == request.user.pk:
        return redirect("cockpit_today")
    if request.method == "POST":
        if verify_account_code(request.user, request.POST.get("code", "")):
            request.session[SESSION_FLAG] = request.user.pk
            request.session.cycle_key()
            return redirect("cockpit_today")
        messages.error(request, "Code unavailable or invalid. Check your authenticator clock or try later.")
    return render(request, "totp_challenge.html")


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def configure(request):
    existing = OwnerTOTP.objects.filter(owner=request.user).first()
    if existing and request.session.get(SESSION_FLAG) != request.user.pk:
        return redirect("totp_challenge")
    if request.method == "POST" and request.POST.get("intent") == "disable":
        if not existing:
            messages.error(request, "No authenticator is enabled.")
        elif not request.user.check_password(request.POST.get("password", "")):
            messages.error(request, "Your password was not accepted.")
        elif not verify_account_code(request.user, request.POST.get("code", "")):
            messages.error(request, "The authenticator code was not accepted.")
        else:
            existing.delete()
            request.session.pop(SESSION_FLAG, None)
            request.session.pop(ENROLL_SECRET, None)
            request.session.cycle_key()
            messages.success(request, "Authenticator disabled. Password login remains required.")
            return redirect("totp_setup")
    elif request.method == "POST" and request.POST.get("intent") == "enable":
        try:
            candidate = decrypt_secret(request.session.get(ENROLL_SECRET, ""))
        except Exception:
            candidate = None
            request.session.pop(ENROLL_SECRET, None)
        if existing:
            messages.error(request, "An authenticator is already enabled.")
        elif not candidate:
            messages.error(request, "Begin setup again.")
        elif not request.user.check_password(request.POST.get("password", "")):
            messages.error(request, "Your password was not accepted.")
        else:
            step = matching_step(candidate, request.POST.get("code", ""))
            if step is None:
                messages.error(request, "Check your new authenticator code and device time.")
            else:
                OwnerTOTP.objects.create(
                    owner=request.user, secret_ciphertext=encrypt_secret(candidate),
                    enabled_at=timezone.now(), last_verified_step=step,
                )
                request.session.pop(ENROLL_SECRET, None)
                request.session[SESSION_FLAG] = request.user.pk
                request.session.cycle_key()
                messages.success(request, "Authenticator enabled for future sign-ins.")
                return redirect("totp_setup")
    elif request.method == "POST":
        messages.error(request, "Unsupported authenticator action.")

    if not existing and ENROLL_SECRET not in request.session:
        # Session storage is signed, not encrypted by default; encrypt the
        # short-lived unenrolled seed before persisting it there.
        request.session[ENROLL_SECRET] = encrypt_secret(generate_secret())
    if not existing:
        try:
            secret = decrypt_secret(request.session[ENROLL_SECRET])
        except Exception:
            # A stale session after a key rotation is not a usable seed.
            secret = generate_secret()
            request.session[ENROLL_SECRET] = encrypt_secret(secret)
    else:
        secret = None
    return render(request, "totp_setup.html", {
        "enabled": existing is not None,
        "setup_secret": secret,
        "provisioning_uri": provisioning_uri(request.user.get_username(), secret)
        if secret else "",
    })
