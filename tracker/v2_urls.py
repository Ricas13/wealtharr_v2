"""Stable URLs for the phased, server-rendered Wealtharr v2 design."""
from django.urls import path

from . import v2_views

urlpatterns = [
    path("overview/", v2_views.overview, name="v2_overview"),
    path(
        "s/<int:instance_id>/<str:section>/",
        v2_views.instance_page,
        name="v2_instance_page",
    ),
    path("catalogue/", v2_views.catalogue, name="v2_catalogue"),
    path("settings/", v2_views.settings, name="v2_settings"),
    path("add-strategy/", v2_views.add_strategy, name="v2_add_strategy"),
]
