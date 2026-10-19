"""Deny private/admin endpoints until the enabled owner factor is checked.

Middleware is deliberately after AuthenticationMiddleware, before Messages.
No alternate Django admin route, app bookmark, or POST can bypass it.
"""
from django.shortcuts import redirect
from django.urls import reverse

from .models import OwnerTOTP
from .totp_views import SESSION_FLAG


class RequireOwnerTOTP:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            path = request.path_info
            allowed = {
                reverse("totp_challenge"),
                reverse("totp_setup"),
                reverse("logout"),
                reverse("health"),
            }
            if (path not in allowed and
                    not path.startswith("/static/") and
                    OwnerTOTP.objects.filter(owner=user).exists() and
                    request.session.get(SESSION_FLAG) != user.pk):
                return redirect("totp_challenge")
        return self.get_response(request)
