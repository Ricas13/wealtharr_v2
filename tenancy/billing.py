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

from . import audit
from .models import BillingEvent, Plan, UserAccess

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


def _assign(user, plan, customer_id, event_type):
    access, _ = UserAccess.objects.get_or_create(user=user, defaults={"plan": plan})
    before = access.plan_id
    access.plan = plan
    if customer_id:
        access.stripe_customer_id = customer_id
    access.save()
    audit.record("billing.plan_assigned", actor=None, subject=user.pk, target=access,
                 metadata={"plan": plan.slug, "from_plan_id": before, "via": event_type})


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
        return f"assigned {plan.slug}"
    if kind == "customer.subscription.deleted":
        access = UserAccess.objects.filter(stripe_customer_id=obj.get("customer") or "-").first()
        default = Plan.objects.filter(is_default=True).first()
        if access is None or default is None:
            return "ignored: unknown customer"
        _assign(access.user, default, "", kind)
        return f"reverted to {default.slug}"
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


PAYPAL_ENDED = ("BILLING.SUBSCRIPTION.CANCELLED", "BILLING.SUBSCRIPTION.EXPIRED", "BILLING.SUBSCRIPTION.SUSPENDED")


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
    if kind in PAYPAL_ENDED:
        sub = ProviderSubscription.objects.filter(
            provider="paypal", external_id=str(resource.get("id") or "-")).select_related("user").first()
        default = Plan.objects.filter(is_default=True).first()
        if sub is None or default is None:
            return "ignored: unknown subscription"
        _assign(sub.user, default, "", kind)
        return f"reverted to {default.slug}"
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
