"""Tenants and who belongs to them.

An Organization is the tenant and the billing boundary. Every registered user starts
with one personal Organization they own; sharing it with others is a later Membership.
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


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


class AuditImmutable(RuntimeError):
    """An audit event may be added, never changed or removed."""


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AuditImmutable("Audit events are append-only.")

    def delete(self):
        raise AuditImmutable("Audit events are append-only.")

    def bulk_update(self, *args, **kwargs):
        raise AuditImmutable("Audit events are append-only.")

    def bulk_create(self, *args, **kwargs):
        raise AuditImmutable("Write audit events with tenancy.audit.record() so the hash chain stays intact.")


class AuditChain(models.Model):
    """The tip of one hash chain; locked while an event is appended so chains never fork."""
    key = models.CharField(max_length=40, unique=True)  # "platform" or "org:<id>"
    last_hash = models.CharField(max_length=64)
    last_event_id = models.BigIntegerField(null=True)


class AuditEvent(models.Model):
    """One privileged or security-relevant action. Append-only and hash-chained.

    Actor and organization are plain ids, not foreign keys, so deleting a user or an
    organization never rewrites history. The database also refuses UPDATE and DELETE.
    """
    occurred_at = models.DateTimeField(default=timezone.now)
    chain = models.CharField(max_length=40, db_index=True)
    actor_id = models.BigIntegerField(null=True, blank=True)
    actor_label = models.CharField(max_length=150, blank=True)
    organization_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    action = models.CharField(max_length=80, db_index=True)
    target_type = models.CharField(max_length=60, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    prev_hash = models.CharField(max_length=64)
    hash = models.CharField(max_length=64, unique=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["id"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AuditImmutable("Audit events are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditImmutable("Audit events are append-only.")

    def __str__(self):
        return "%s %s" % (self.occurred_at.isoformat(), self.action)
