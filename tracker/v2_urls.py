"""Stable URLs for the phased, server-rendered Wealtharr v2 design."""
from django.urls import path

from . import v2_views, v2_ledger_views, v2_review, v2_settings, v2_go_live, v2_strategy, v2_home, v2_study

urlpatterns = [
    path("overview/", v2_views.overview, name="v2_overview"),
    # Before the generic section route, which would otherwise capture it.
    path("s/<int:instance_id>/study-window.json", v2_study.study_window_json,
         name="v2_study_window_json"),
    path("s/<int:instance_id>/study-simulate.json", v2_study.study_simulate_json,
         name="v2_study_simulate_json"),
    path("s/<int:instance_id>/go-live/", v2_go_live.go_live_view, name="v2_go_live"),
    path("s/<int:instance_id>/plan/", v2_strategy.plan_view, name="v2_strategy_plan"),
    path(
        "s/<int:instance_id>/<str:section>/",
        v2_views.instance_page,
        name="v2_instance_page",
    ),
    path("s/<int:instance_id>/ledger/new/",
         v2_ledger_views.edit_entry, {"operation": "new"}, name="v2_ledger_add"),
    path("s/<int:instance_id>/ledger/<int:entry_id>/<str:operation>/",
         v2_ledger_views.edit_entry, name="v2_ledger_modify"),
    path("s/<int:instance_id>/review/<int:signal_id>/",
         v2_review.guided_review, name="v2_guided_review"),
    path("s/<int:instance_id>/notice/<int:signal_id>/feedback/",
         v2_review.notice_feedback, name="v2_notice_feedback"),
    path("settings/", v2_views.settings, name="v2_settings"),
    path("settings/discord/connect/", v2_settings.discord_connect, name="discord_connect"),
    path("settings/discord/callback/", v2_settings.discord_callback, name="discord_callback"),
    path("settings/discord/disconnect/", v2_settings.discord_disconnect, name="discord_disconnect"),
    path("settings/test/<str:channel>/",
         v2_settings.test_channel, name="v2_notification_test"),
    path("add-strategy/", v2_views.add_strategy, name="v2_add_strategy"),
    path("add-strategy/compare.json", v2_home.compare_named_json, name="v2_compare_json"),
]
