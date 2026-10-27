"""Shared behaviour for models that belong to one organization."""
from django.core.exceptions import ValidationError
from django.db import models


class OrganizationOwned(models.Model):
    """Adds ``organization`` and fills it in on save.

    The column is nullable during the migration from the single-owner model; the enforcing
    step makes it NOT NULL once the backfill is verified. Until then an unset value is
    stamped from the owner's personal organization so new rows never escape the tenant.
    """

    organization = models.ForeignKey(
        "tenancy.Organization", null=True, blank=True, editable=False,
        on_delete=models.PROTECT, related_name="%(app_label)s_%(class)s_set")

    class Meta:
        abstract = True

    def _tenant_owner(self):
        return self.owner

    def _default_organization(self):
        from .services import personal_org_for
        return personal_org_for(self._tenant_owner())

    def save(self, *args, **kwargs):
        if self.organization_id is None:
            self.organization = self._default_organization()
            fields = kwargs.get("update_fields")
            if fields is not None:
                kwargs["update_fields"] = set(fields) | {"organization"}
        self._check_organization()
        return super().save(*args, **kwargs)

    def _check_organization(self):
        """Hook: raise ValidationError when the organization disagrees with a parent."""
