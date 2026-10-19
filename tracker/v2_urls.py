"""Stable URLs for the phased, server-rendered Wealtharr v2 design."""
from django.urls import path

from . import v2_views, v2_ledger_views, v2_today

urlpatterns = [
    path("overview/", v2_views.overview, name="v2_overview"),
    path(
        "s/<int:instance_id>/<str:section>/",
        v2_views.instance_page,
        name="v2_instance_page",
    ),
    path("s/<int:instance_id>/ledger/new/",
         v2_ledger_views.edit_entry, {"operation": "new"}, name="v2_ledger_add"),
    path("s/<int:instance_id>/ledger/<int:entry_id>/<str:operation>/",
         v2_ledger_views.edit_entry, name="v2_ledger_modify"),
    path("s/<int:instance_id>/ledger/import/",
         v2_ledger_views.import_csv, name="v2_ledger_import"),
    path("s/<int:instance_id>/review/<int:signal_id>/",
         v2_today.guided_review, name="v2_guided_review"),
    path("catalogue/", v2_views.catalogue, name="v2_catalogue"),
    path("settings/", v2_views.settings, name="v2_settings"),
    path("add-strategy/", v2_views.add_strategy, name="v2_add_strategy"),
]
