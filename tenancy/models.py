"""Tenants and who belongs to them.

An Organization is the tenant and the billing boundary. Every registered user starts
with one personal Organization they own; sharing it with others is a later Membership.
"""
import uuid

from django.conf import settings
from django.db import models


class Organization(models.Model):
    ACTIVE, SUSPENDED, PENDING_DELETION = "active", "suspended", "pending_deletion"
    STATUSES = [(ACTIVE, "Active"), (SUSPENDED, "Suspended"), (PENDING_DELETION, "Pending deletion")]

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=120)
    # Set for a user's own organization; the unique constraint makes "get or create" race-safe.
    personal_owner = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="personal_organization")
    status = models.CharField(max_length=20, choices=STATUSES, default=ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Membership(models.Model):
    OWNER, ADMIN, MEMBER, VIEWER = "owner", "admin", "member", "viewer"
    ROLES = [(OWNER, "Owner"), (ADMIN, "Admin"), (MEMBER, "Member"), (VIEWER, "Viewer")]
    # Which roles may change data. A viewer is read-only.
    WRITE_ROLES = frozenset((OWNER, ADMIN, MEMBER))
    ADMIN_ROLES = frozenset((OWNER, ADMIN))

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=10, choices=ROLES, default=MEMBER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "organization"], name="unique_membership")]

    @property
    def can_write(self):
        return self.role in self.WRITE_ROLES

    @property
    def can_administer(self):
        return self.role in self.ADMIN_ROLES

    def __str__(self):
        return "%s in %s (%s)" % (self.user_id, self.organization_id, self.role)
