from django.apps import AppConfig


class TrackerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tracker"

    def ready(self):
        from . import features_catalog  # noqa: F401  (declares the features plans can include)
