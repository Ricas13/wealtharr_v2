"""Stripe webhook handling. Inactive unless a webhook secret is saved on the admin Payments card (or set in the environment).

Only two events matter: a completed checkout assigns the plan whose Payment Link was used to
the user named in ``client_reference_id``; a deleted subscription puts that customer back on
the default plan. Signatures are checked here (HMAC-SHA256 over ``t.payload``) so no SDK is needed.
"""
import hashlib
import hmac
import json
import time

from django.db import IntegrityError, transaction

import logging

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import audit
from .mail import send_system_email
from .models import BillingEvent, Plan, UserAccess

LOG = logging.getLogger(__name__)

TOLERANCE_SECONDS = 300


def verify_signature(payload, header, secret, now=None):
    """True when ``header`` (Stripe-Signature) matches ``payload`` and is recent."""
    if not secret or not header:
        return False
    try:
        parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
        stamp = int(parts["t"])
    except (KeyError, ValueError):
        return False
    if abs((now if now is not None else time.time()) - stamp) > TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
    candidates = [v for k, v in (p.split("=", 1) for p in header.split(",") if "=" in p) if k == "v1"]
    return any(hmac.compare_digest(expected, c) for c in candidates)


def _user_for(client_reference_id):
    from django.contrib.auth import get_user_model
    try:
        return get_user_model().objects.filter(pk=int(client_reference_id), is_active=True).first()
    except (TypeError, ValueError):
        return None


def _notify(user, subject, body):
    """Tell the person by email. Never raises; no address, no email."""
    if getattr(user, "email", ""):
        send_system_email(user.email, subject, body)


def _assign(user, plan, customer_id, event_type):
    access, created = UserAccess.objects.get_or_create(user=user, defaults={"plan": plan})
    before = None if created else access.plan_id  # a brand-new row means they were on the default plan
    access.plan = plan
    if customer_id:
        access.stripe_customer_id = customer_id
    access.save()
    audit.record("billing.plan_assigned", actor=None, subject=user.pk, target=access,
                 metadata={"plan": plan.slug, "from_plan_id": before, "via": event_type})
    if before != plan.pk and not (created and plan.is_default):
        _notify(user, "Your Wealtharr plan changed",
                f"You are now on the {plan.name} plan. You can see what it includes, and manage billing, on the Your plan page.")


def _end_access(user, via, subscription=None):
    """Back to the default plan, unless another subscription of theirs is still running."""
    from tracker.models import ProviderSubscription
    default = Plan.objects.filter(is_default=True).first()
    if default is None:
        return "ignored: no default plan"
    others = ProviderSubscription.objects.filter(user=user, ended_at__isnull=True)
    if subscription is not None:
        others = others.exclude(pk=subscription.pk)
    if others.exists():
        return "kept: another subscription is active"
    _assign(user, default, "", via)
    return f"reverted to {default.slug}"


def expire_due_subscriptions(now=None):
    """Cancelled-with-time-left subscriptions whose paid period is over. Run by the hourly job."""
    from tracker.models import ProviderSubscription
    now = now or timezone.now()
    count = 0
    for sub in ProviderSubscription.objects.filter(ended_at__isnull=True, ends_at__lte=now).select_related("user"):
        sub.ended_at = now
        sub.save(update_fields=["ended_at"])
        _end_access(sub.user, "paid period ended", sub)
        count += 1
    return count


def _plan_for_stripe_session(obj):
    """The plan bought: the one our own checkout named (and that has a Stripe price), else by Payment Link."""
    from tracker.models import PlanProviderMapping
    slug = (obj.get("metadata") or {}).get("plan_slug")
    if slug:
        mapped = PlanProviderMapping.objects.filter(provider="stripe", plan__slug=slug).select_related("plan").first()
        if mapped:
            return mapped.plan
    return Plan.objects.filter(stripe_payment_link_id=obj.get("payment_link") or "-").first()


def apply_event(event):
    """Apply one verified event; returns a short outcome string."""
    kind = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    if kind == "checkout.session.completed":
        if obj.get("payment_status") not in ("paid", "no_payment_required"):
            return "ignored: not paid"
        user = _user_for(obj.get("client_reference_id"))
        plan = _plan_for_stripe_session(obj)
        if user is None or plan is None:
            return "ignored: unknown user or payment link"
        _assign(user, plan, obj.get("customer") or "", kind)
        if obj.get("subscription"):
            from tracker.models import ProviderSubscription
            ProviderSubscription.objects.get_or_create(
                provider="stripe", external_id=str(obj["subscription"])[:80], defaults={"user": user})
        return f"assigned {plan.slug}"
    if kind == "customer.subscription.deleted":
        from tracker.models import ProviderSubscription
        sub = ProviderSubscription.objects.filter(
            provider="stripe", external_id=str(obj.get("id") or "-")).select_related("user").first()
        if sub is not None:
            sub.ended_at = timezone.now()
            sub.save(update_fields=["ended_at"])
            return _end_access(sub.user, kind, sub)
        access = UserAccess.objects.filter(stripe_customer_id=obj.get("customer") or "-").first()
        default = Plan.objects.filter(is_default=True).first()
        if access is None or default is None:
            return "ignored: unknown customer"
        _assign(access.user, default, "", kind)
        return f"reverted to {default.slug}"
    if kind == "invoice.payment_failed":
        access = UserAccess.objects.filter(stripe_customer_id=obj.get("customer") or "-").select_related("user").first()
        if access is None:
            return "ignored: unknown customer"
        _notify(access.user, "Your Wealtharr payment failed",
                "Your last payment did not go through. Open Your plan, choose Manage billing and update your payment method. "
                "If it keeps failing the subscription ends and you return to the default plan.")
        return "notified: payment failed"
    return "ignored: event type"


def _record_and_apply(provider, event, apply):
    """Store the event id once and apply it, all or nothing. (status, text)."""
    try:
        event_id = str(event["id"])[:120]
    except (KeyError, TypeError):
        return 400, "bad payload"
    try:
        with transaction.atomic():
            row = BillingEvent.objects.create(provider=provider, event_id=event_id,
                                              event_type=str(event.get("event_type") or event.get("type", ""))[:80])
            row.outcome = apply(event)[:200]
            row.save(update_fields=["outcome"])
    except IntegrityError:
        return 200, "duplicate"
    return 200, "ok"


def process(payload, header, now=None):
    """Stripe: (http_status, text). Verifies, de-duplicates, applies; all-or-nothing."""
    from . import gateways
    secret = gateways.effective("stripe").webhook_secret
    if not secret:
        return 404, "not enabled"
    if not verify_signature(payload, header, secret, now):
        return 400, "bad signature"
    try:
        event = json.loads(payload)
    except ValueError:
        return 400, "bad payload"
    return _record_and_apply("stripe", event, apply_event)


PAYPAL_ENDED_NOW = ("BILLING.SUBSCRIPTION.EXPIRED", "BILLING.SUBSCRIPTION.SUSPENDED")


def _paypal_sub(resource):
    from tracker.models import ProviderSubscription
    return ProviderSubscription.objects.filter(
        provider="paypal", external_id=str(resource.get("id") or "-")).select_related("user").first()


def apply_paypal_event(event):
    from tracker.models import PlanProviderMapping, ProviderSubscription
    kind = event.get("event_type", "")
    resource = event.get("resource") or {}
    if kind == "BILLING.SUBSCRIPTION.ACTIVATED":
        user = _user_for(resource.get("custom_id"))
        mapped = PlanProviderMapping.objects.filter(
            provider="paypal", external_id=resource.get("plan_id") or "-").select_related("plan").first()
        if user is None or mapped is None or not resource.get("id"):
            return "ignored: unknown user or plan"
        ProviderSubscription.objects.get_or_create(
            provider="paypal", external_id=str(resource["id"])[:80], defaults={"user": user})
        _assign(user, mapped.plan, "", kind)
        return f"assigned {mapped.plan.slug}"
    sub = _paypal_sub(resource)
    if kind == "BILLING.SUBSCRIPTION.CANCELLED":
        if sub is None:
            return "ignored: unknown subscription"
        paid_until = parse_datetime((resource.get("billing_info") or {}).get("next_billing_time") or "")
        if paid_until is not None and timezone.is_naive(paid_until):
            paid_until = timezone.make_aware(paid_until, timezone.utc)
        if paid_until is not None and paid_until > timezone.now():
            sub.ends_at = paid_until
            sub.save(update_fields=["ends_at"])
            _notify(sub.user, "Your Wealtharr subscription was cancelled",
                    f"Your plan stays active until {paid_until:%d %b %Y}, then you return to the default plan.")
            return "scheduled end"
        sub.ended_at = timezone.now()
        sub.save(update_fields=["ended_at"])
        return _end_access(sub.user, kind, sub)
    if kind in PAYPAL_ENDED_NOW:
        if sub is None:
            return "ignored: unknown subscription"
        sub.ended_at = timezone.now()
        sub.save(update_fields=["ended_at"])
        return _end_access(sub.user, kind, sub)
    if kind == "BILLING.SUBSCRIPTION.PAYMENT.FAILED":
        if sub is None:
            return "ignored: unknown subscription"
        _notify(sub.user, "Your Wealtharr payment failed",
                "PayPal could not take your last payment. Check your PayPal funding source; PayPal will retry, and if it keeps failing the subscription ends.")
        return "notified: payment failed"
    return "ignored: event type"


def process_paypal(headers, body_text):
    """PayPal: the delivery is confirmed with PayPal itself before anything is applied."""
    from . import gateways
    if not gateways.status("paypal")["checkout_ready"]:
        return 404, "not enabled"
    if not gateways.verify_paypal_webhook(headers, body_text):
        return 400, "bad signature"
    try:
        event = json.loads(body_text)
    except ValueError:
        return 400, "bad payload"
    return _record_and_apply("paypal", event, apply_paypal_event)
