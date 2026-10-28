"""The audit log. (The tenant is the user, so there are no organizations or memberships.)"""
from django.conf import settings
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


class Plan(models.Model):
    """A named bundle of features, configured in the admin. "Full" grants everything.

    The payment links are plain links to a page the owner made on Stripe or PayPal; the app
    does not learn from them that someone paid, so plans are assigned to users by hand for now.
    """
    slug = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=80)
    description = models.TextField(blank=True)
    price_label = models.CharField(max_length=60, blank=True, help_text='Shown as written, e.g. "£5 / month".')
    stripe_link = models.URLField(blank=True, help_text="A Stripe Payment Link or Checkout link (https only).")
    paypal_link = models.URLField(blank=True, help_text="A PayPal payment or subscription link (https only).")
    stripe_payment_link_id = models.CharField(
        max_length=80, blank=True,
        help_text='The Payment Link\'s id ("plink_..."). When set, a paid Stripe checkout for it assigns this plan automatically.')
    is_public = models.BooleanField(default=False, help_text="May be offered to users on a plans page.")
    sort_order = models.PositiveSmallIntegerField(default=0)
    all_features = models.BooleanField(
        default=False, help_text="Grant every feature, including ones added later.")
    features = models.JSONField(default=list, blank=True)
    limits = models.JSONField(default=dict, blank=True, help_text="Numeric limits, e.g. {\"strategies.max\": 2}")
    is_default = models.BooleanField(default=False, help_text="Given to users who have no plan of their own.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name"]
        constraints = [models.UniqueConstraint(
            fields=["is_default"], condition=models.Q(is_default=True), name="one_default_plan")]

    def save(self, *args, **kwargs):
        if self.is_default:  # at most one default
            type(self).objects.exclude(pk=self.pk).filter(is_default=True).update(is_default=False)
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class UserAccess(models.Model):
    """A user's plan, with optional per-user feature grants and denials."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="access")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="users")
    grant = models.JSONField(default=list, blank=True)
    deny = models.JSONField(default=list, blank=True)
    note = models.CharField(max_length=200, blank=True)
    stripe_customer_id = models.CharField(max_length=80, blank=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "plan and features"
        verbose_name_plural = "plan and features"

    def __str__(self):
        return "%s: %s" % (self.user_id, self.plan)


class BillingEvent(models.Model):
    """One processed payment-provider event. The unique id makes redelivery harmless."""
    provider = models.CharField(max_length=20, default="stripe")
    event_id = models.CharField(max_length=120)
    event_type = models.CharField(max_length=80)
    outcome = models.CharField(max_length=200, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]
        constraints = [models.UniqueConstraint(fields=["provider", "event_id"], name="one_billing_event")]
