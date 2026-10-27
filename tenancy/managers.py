"""Managers that only ever return the active tenant's rows."""
from django.db import models

from .context import require_organization_id


class TenantManager(models.Manager):
    """Filters on the active organization and raises when there is none.

    ``scope`` is the path to the organization: ``"organization"`` for rows that carry it,
    ``"strategy__organization"`` for rows reached through their strategy.
    """

    def __init__(self, scope="organization"):
        super().__init__()
        self.scope = scope

    def get_queryset(self):
        return super().get_queryset().filter(**{self.scope + "_id": require_organization_id()})
