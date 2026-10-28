from django.apps import AppConfig


class TenancyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tenancy"

    def ready(self):
        from . import signals  # noqa: F401  (connects the authentication receivers)

        # Operator-run commands are trusted platform code: with row-level security on they must
        # see everyone's rows (they scope themselves with per_tenant where it matters).
        from django.core.management.base import BaseCommand

        from .context import platform_context
        original = BaseCommand.execute

        def execute(self, *args, **options):
            with platform_context():
                return original(self, *args, **options)

        BaseCommand.execute = execute
