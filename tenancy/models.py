"""The audit log. (The tenant is the user, so there are no organizations or memberships.)"""
from django.db import models
from django.utils import timezone


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
    key = models.CharField(max_length=40, unique=True)  # "platform" or "user:<id>"
    last_hash = models.CharField(max_length=64)
    last_event_id = models.BigIntegerField(null=True)


class AuditEvent(models.Model):
    """One privileged or security-relevant action. Append-only and hash-chained.

    Actor and subject are plain ids, not foreign keys, so deleting a user never rewrites
    history. The database also refuses UPDATE and DELETE.
    """
    occurred_at = models.DateTimeField(default=timezone.now)
    chain = models.CharField(max_length=40, db_index=True)
    actor_id = models.BigIntegerField(null=True, blank=True)
    actor_label = models.CharField(max_length=150, blank=True)
    # Only events written before organizations were removed carry this; new events use subject_id.
    organization_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    # The user whose data the event is about (the actor for self-service actions).
    subject_id = models.BigIntegerField(null=True, blank=True, db_index=True)
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
