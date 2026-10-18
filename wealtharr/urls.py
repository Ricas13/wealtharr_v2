from django.contrib import admin
from django.urls import path
from tracker import views

urlpatterns = [
    path("", views.home, name="home"),
    path("login/", views.sign_in, name="login"),
    path("logout/", views.sign_out, name="logout"),
    path("app/", views.dashboard, name="dashboard"),
    path("app/accounts/", views.accounts, name="accounts"),
    path("app/strategies/new/", views.new_strategy, name="new_strategy"),
    path("app/strategies/<int:pk>/", views.strategy_detail, name="strategy_detail"),
    path("app/strategies/<int:pk>/ledger/", views.record_entry, name="record_entry"),
    path("app/strategies/<int:pk>/price/", views.record_price, name="record_price"),
    path("app/strategies/<int:pk>/refresh/", views.refresh_market, name="refresh_market"),
    path("app/strategies/<int:pk>/signal/<int:signal_pk>/ack/", views.acknowledge_signal, name="acknowledge_signal"),
    path("app/snapshot/", views.capture_snapshot, name="capture_snapshot"),
    path("app/settings/", views.user_settings, name="user_settings"),
    path("app/settings/password/", views.change_password, name="change_password"),
    path("control/", views.control, name="control"),
    path("control/test/<str:channel>/", views.test_notification, name="test_notification"),
    path("django-admin/", admin.site.urls),
    path("health/", views.health, name="health"),
]
