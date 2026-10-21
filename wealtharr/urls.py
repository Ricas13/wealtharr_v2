"""Wealtharr has one owner UI: v2."""
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from tracker import totp_views, v2_auth

urlpatterns = [
    path("", v2_auth.home, name="home"),
    path("login/", v2_auth.sign_in, name="login"),
    path("logout/", v2_auth.sign_out, name="logout"),
    path("security/authenticator/", totp_views.configure, name="totp_setup"),
    path("security/verify/", totp_views.challenge, name="totp_challenge"),
    path("", include("tracker.v2_urls")),

    path("settings/password/", v2_auth.change_password, name="change_password"),

    path("control/", v2_auth.control, name="control"),
    path("django-admin/login/",
         RedirectView.as_view(pattern_name="login", permanent=False),
         name="private_admin_login"),
    path("django-admin/", admin.site.urls),
    path("health/", v2_auth.health, name="health"),
]
