from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from tracker import views, totp_views

urlpatterns = [
    path("", views.home, name="home"),
    path("login/", views.sign_in, name="login"),
    path("logout/", views.sign_out, name="logout"),
    path("security/authenticator/", totp_views.configure, name="totp_setup"),
    path("security/verify/", totp_views.challenge, name="totp_challenge"),
    path("cockpit/", include("tracker.cockpit_urls")),
    # Phased Django/template redesign: isolated from the existing financial cockpit.
    path("", include("tracker.v2_urls")),
    path("app/", views.dashboard, name="dashboard"),
    path("app/accounts/", views.accounts, name="accounts"),
    path("app/strategies/new/", views.new_strategy, name="new_strategy"),
    path("app/9sig/start/", views.start_real_9sig, name="start_real_9sig"),
    path("app/strategies/<int:pk>/", views.strategy_detail, name="strategy_detail"),
    path("app/strategies/<int:pk>/ledger/", views.record_entry, name="record_entry"),
    path("app/strategies/<int:pk>/price/", views.record_price, name="record_price"),
    path("app/strategies/<int:pk>/refresh/", views.refresh_market, name="refresh_market"),
    path("app/strategies/<int:pk>/monitor/", views.save_automation, name="save_automation"),
    path("app/strategies/<int:pk>/rebaseline/", views.confirm_strategy_reset, name="confirm_strategy_reset"),
    path("app/strategies/<int:pk>/signal/<int:signal_pk>/ack/", views.acknowledge_signal, name="acknowledge_signal"),
    path("app/strategies/<int:pk>/signal/<int:signal_pk>/review/", views.review_signal, name="review_signal"),
    path("app/snapshot/", views.capture_snapshot, name="capture_snapshot"),
    path("app/settings/", views.user_settings, name="user_settings"),
    path("app/settings/password/", views.change_password, name="change_password"),
    path("control/", views.control, name="control"),
    path("control/test/<str:channel>/", views.test_notification, name="test_notification"),
    # Force admin password entry through the standard Turnstile-protected login.
    # Once signed in, optional owner TOTP middleware also gates admin pages.
    path("django-admin/login/", RedirectView.as_view(pattern_name="login", permanent=False), name="private_admin_login"),
    path("django-admin/", admin.site.urls),
    path("health/", views.health, name="health"),
]
