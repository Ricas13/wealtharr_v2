"""Managers that only ever return the active user's rows."""
from django.db import models

from .context import require_tenant_id


class TenantManager(models.Manager):
    """Filters on the active user and raises when there is none.

    ``scope`` is the path to the owning user: ``"owner"`` for rows that carry it, or e.g.
    ``"strategy__owner"`` for rows reached through their strategy.
    """

    def __init__(self, scope="owner"):
        super().__init__()
        self.scope = scope

    def get_queryset(self):
        return super().get_queryset().filter(**{self.scope + "_id": require_tenant_id()})
