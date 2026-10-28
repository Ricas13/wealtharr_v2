"""Wealtharr has one owner UI: v2."""
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from tracker import guides, pwa, seo, totp_views, v2_auth, v2_home

from tenancy import support, views as tenancy_views
from tenancy import ops
from django.contrib.auth import views as auth_views
from django.urls import reverse_lazy
from tenancy import accounts

urlpatterns = [
    path("", v2_home.landing, name="home"),
    path("compare.json", v2_home.compare_json, name="home_compare"),
    path("invite/", v2_home.request_invite, name="request_invite"),
    path("guides/", guides.guides_index, name="guides"),
    path("guides/<slug:slug>/", guides.guide, name="guide"),
    path("privacy/", guides.privacy, name="privacy"),
    path("terms/", guides.terms, name="terms"),
    path("robots.txt", seo.robots_txt, name="robots_txt"),
    path(".well-known/security.txt", seo.security_txt, name="security_txt"),
    path("sitemap.xml", seo.sitemap_xml, name="sitemap_xml"),
    path("site.webmanifest", seo.web_manifest, name="web_manifest"),
    path("sw.js", pwa.service_worker, name="service_worker"),
    path("offline/", pwa.offline, name="offline"),
    path("login/", v2_auth.sign_in, name="login"),
    path("signup/", accounts.signup, name="signup"),
    path("signup/verify/<str:token>/", accounts.verify_email, name="verify_email"),
    path("password-reset/", accounts.RateLimitedResetView.as_view(), name="password_reset"),
    path("password-reset/sent/", auth_views.PasswordResetDoneView.as_view(
        template_name="tenancy/reset_done.html"), name="password_reset_done"),
    path("password-reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(
        template_name="tenancy/reset_confirm.html", success_url=reverse_lazy("password_reset_complete")),
        name="password_reset_confirm"),
    path("password-reset/complete/", auth_views.PasswordResetCompleteView.as_view(
        template_name="tenancy/reset_complete.html"), name="password_reset_complete"),
    path("account/", accounts.account_page, name="account"),
    path("account/sessions/revoke/", accounts.revoke_sessions, name="revoke_sessions"),
    path("account/export/", accounts.export_download, name="account_export"),
    path("account/delete/", accounts.delete_account, name="delete_account"),
    path("logout/", v2_auth.sign_out, name="logout"),
    path("security/authenticator/", totp_views.configure, name="totp_setup"),
    path("security/verify/", totp_views.challenge, name="totp_challenge"),
    path("", include("tracker.v2_urls")),

    path("settings/password/", v2_auth.change_password, name="change_password"),

    path("control/", v2_auth.control, name="control"),
    path("control/prices/<str:symbol>/accept/", v2_auth.accept_price_revision, name="accept_price_revision"),
    path("django-admin/login/",
         RedirectView.as_view(pattern_name="login", permanent=False),
         name="private_admin_login"),
    path("django-admin/", admin.site.urls),
    path("health/", v2_auth.health, name="health"),
    path("ready/", ops.ready, name="ready"),
    path("webhooks/stripe/", tenancy_views.stripe_webhook, name="stripe_webhook"),
    path("webhooks/paypal/", tenancy_views.paypal_webhook, name="paypal_webhook"),
    path("plans/", tenancy_views.plans_page, name="plans"),
    path("control/payments/<str:provider>/test/", tenancy_views.gateway_test, name="gateway_test"),
    path("control/payments/<str:provider>/environment/", tenancy_views.gateway_use_environment,
         name="gateway_use_environment"),
    path("plans/return/", tenancy_views.plans_return, name="plans_return"),
    path("plans/<slug:slug>/buy/<str:provider>/", tenancy_views.checkout, name="plan_checkout"),
    path("support-access/", support.my_support_access, name="support_access"),
    path("support-access/off/", support.revoke_support_access, name="revoke_support_access"),
    path("support/", support.support_index, name="support_index"),
    path("support/<int:user_id>/", support.support_view, name="support_view"),
]
