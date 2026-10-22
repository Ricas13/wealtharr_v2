"""Wealtharr has one owner UI: v2."""
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from tracker import guides, seo, totp_views, v2_auth, v2_home

urlpatterns = [
    path("", v2_home.landing, name="home"),
    path("compare.json", v2_home.compare_json, name="home_compare"),
    path("invite/", v2_home.request_invite, name="request_invite"),
    path("guides/", guides.guides_index, name="guides"),
    path("guides/<slug:slug>/", guides.guide, name="guide"),
    path("privacy/", guides.privacy, name="privacy"),
    path("robots.txt", seo.robots_txt, name="robots_txt"),
    path("sitemap.xml", seo.sitemap_xml, name="sitemap_xml"),
    path("site.webmanifest", seo.web_manifest, name="web_manifest"),
    path("login/", v2_auth.sign_in, name="login"),
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
]
