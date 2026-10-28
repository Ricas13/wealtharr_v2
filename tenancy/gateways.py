"""Payment gateways (Stripe, PayPal), configured on the admin page like mail, Discord and Telegram.

Credentials are saved encrypted as one blob per gateway; with nothing saved the gateway falls back
to environment settings. A gateway can be switched off without losing its credentials. "Ready for
checkout" means credentials and webhook are both set; a plan is sold through a gateway only when its
price/plan id there has been verified. Secrets are never logged, shown again, or put in the audit log.
"""
import base64
import json
import os
import re
from types import SimpleNamespace

from django.conf import settings
from django.utils import timezone

from . import audit
from .gateway_http import GatewayError, call

PROVIDERS = ("stripe", "paypal")
LABELS = {"stripe": "Stripe", "paypal": "PayPal"}
SECRET_FIELDS = {"stripe": ("restricted_key", "secret_key", "webhook_secret"),
                 "paypal": ("client_id", "client_secret", "webhook_id")}
EXTERNAL_ID = re.compile(r"^[A-Za-z0-9_\-]{3,80}$")
STRIPE_API = "https://api.stripe.com/v1"


def paypal_host(environment):
    return "https://api-m.paypal.com" if environment == "live" else "https://api-m.sandbox.paypal.com"


# ---- reading and saving settings ----------------------------------------------------------

def _env(provider):
    if provider == "stripe":
        cfg = {"restricted_key": os.environ.get("STRIPE_RESTRICTED_KEY", ""),
               "secret_key": os.environ.get("STRIPE_SECRET_KEY", ""),
               "webhook_secret": getattr(settings, "STRIPE_WEBHOOK_SECRET", "")}
        off = os.environ.get("STRIPE_ENABLED") == "false"
    else:
        cfg = {"client_id": os.environ.get("PAYPAL_CLIENT_ID", ""),
               "client_secret": os.environ.get("PAYPAL_CLIENT_SECRET", ""),
               "webhook_id": os.environ.get("PAYPAL_WEBHOOK_ID", ""),
               "environment": "live" if os.environ.get("PAYPAL_ENV") == "live" else "sandbox"}
        off = os.environ.get("PAYPAL_ENABLED") == "false"
    cfg["source"] = "environment"
    cfg["enabled"] = (not off) and any(cfg[f] for f in SECRET_FIELDS[provider])
    return cfg


def raw(provider):
    """Saved or environment settings, whether or not the gateway is switched on."""
    from tracker.models import PaymentProviderCredential
    from tracker.services import decrypt_secret
    row = PaymentProviderCredential.objects.filter(provider=provider).first()
    if row is None:
        return _env(provider)
    try:
        secrets = json.loads(decrypt_secret(row.secrets_encrypted) or "{}")
    except Exception:  # unreadable (encryption key changed): treat as no credentials, fail closed
        secrets = {}
    cfg = {f: secrets.get(f, "") for f in SECRET_FIELDS[provider]}
    cfg.update(row.options or {})
    cfg.setdefault("enabled", True)
    cfg["source"] = "database"
    if provider == "paypal":
        cfg["environment"] = "live" if cfg.get("environment") == "live" else "sandbox"
    return cfg


def effective(provider):
    """What the app may use now: a switched-off gateway has no credentials."""
    cfg = raw(provider)
    if not cfg["enabled"]:
        cfg = {**cfg, **{f: "" for f in SECRET_FIELDS[provider]}}
    return SimpleNamespace(**cfg)


def status(provider):
    cfg = raw(provider)
    credentials = (bool(cfg["restricted_key"] or cfg["secret_key"]) if provider == "stripe"
                   else bool(cfg["client_id"] and cfg["client_secret"]))
    webhook = bool(cfg["webhook_secret"] if provider == "stripe" else cfg["webhook_id"])
    return {"provider": provider, "label": LABELS[provider], "source": cfg["source"], "enabled": bool(cfg["enabled"]),
            "credentials": credentials, "webhook": webhook,
            "checkout_ready": bool(cfg["enabled"] and credentials and webhook),
            "environment": cfg.get("environment", ""),
            "secret_set": {f: bool(cfg[f]) for f in SECRET_FIELDS[provider]}}


def checkout_ready(provider):
    return status(provider)["checkout_ready"]


def save(provider, data, *, actor=None, request=None):
    """Keep a secret when its field is blank, replace it when given, clear it when asked."""
    from tracker.models import PaymentProviderCredential
    from tracker.services import encrypt_secret
    current = raw(provider)
    secrets, cleared = {}, []
    for field in SECRET_FIELDS[provider]:
        if data.get("clear_" + field):
            secrets[field] = ""
            cleared.append(field)
        else:
            secrets[field] = (data.get(field) or "").strip() or current[field]
    options = {"enabled": bool(data.get("enabled"))}
    if provider == "paypal":
        options["environment"] = "live" if data.get("environment") == "live" else "sandbox"
    PaymentProviderCredential.objects.update_or_create(
        provider=provider,
        defaults={"secrets_encrypted": encrypt_secret(json.dumps(secrets)), "options": options,
                  "updated_by": actor if getattr(actor, "pk", None) else None})
    audit.record("payments.credentials_saved", actor=actor, subject=None, request=request,
                 metadata={"provider": provider, "enabled": options["enabled"], "cleared": cleared,
                           "environment": options.get("environment", "")})


def use_environment(provider, *, actor=None, request=None):
    from tracker.models import PaymentProviderCredential
    PaymentProviderCredential.objects.filter(provider=provider).delete()
    audit.record("payments.use_environment", actor=actor, subject=None, request=request,
                 metadata={"provider": provider})


# ---- talking to the gateways --------------------------------------------------------------

def _stripe_headers(cfg):
    key = cfg.restricted_key or cfg.secret_key
    if not key:
        raise GatewayError("Stripe credentials are not set up.")
    return {"Authorization": "Bearer " + key}


def paypal_token(cfg):
    if not (cfg.client_id and cfg.client_secret):
        raise GatewayError("PayPal credentials are not set up.")
    basic = base64.b64encode(f"{cfg.client_id}:{cfg.client_secret}".encode()).decode()
    code, body = call("POST", paypal_host(cfg.environment) + "/v1/oauth2/token",
                      headers={"Authorization": "Basic " + basic}, form={"grant_type": "client_credentials"})
    if code != 200 or not body.get("access_token"):
        raise GatewayError(body.get("error_description") or body.get("message") or f"PayPal returned HTTP {code}.")
    return body["access_token"]


def test_connection(provider):
    """(ok, message). Honest about what it proves."""
    cfg = effective(provider)
    try:
        if provider == "stripe":
            key = cfg.restricted_key or cfg.secret_key
            code, body = call("GET", STRIPE_API + "/prices?limit=1", headers=_stripe_headers(cfg))
            if code == 401:
                return False, (body.get("error") or {}).get("message") or "Stripe rejected the key."
            if code == 403:
                return True, ("The key was recognised, but it may lack permission to read prices or create "
                              "Checkout Sessions. Check its permissions, then complete a test checkout.")
            if code != 200:
                return False, f"Stripe returned HTTP {code}."
            if key.startswith("rk_"):
                return True, ("The restricted key works and can read prices. That does not prove it can create "
                              "Checkout Sessions: complete a test checkout before taking real payments.")
            return True, "The key works. Complete a test checkout to prove the whole path, including the webhook."
        paypal_token(cfg)
        return True, "PayPal %s connection works. The client credentials were accepted." % cfg.environment
    except GatewayError as exc:
        return False, str(exc)


def verify_mapping(mapping):
    """Ask the gateway whether this price/plan exists, is active and recurring. Saves the answer."""
    ok, note = False, ""
    if not EXTERNAL_ID.match(mapping.external_id):
        note = "That does not look like a valid id."
    elif not status(mapping.provider)["credentials"]:
        note = "Set up the gateway credentials first."
    else:
        cfg = effective(mapping.provider)
        try:
            if mapping.provider == "stripe":
                code, body = call("GET", f"{STRIPE_API}/prices/{mapping.external_id}", headers=_stripe_headers(cfg))
                if code != 200:
                    note = (body.get("error") or {}).get("message") or f"Stripe returned HTTP {code}."
                elif not body.get("active"):
                    note = "That price is archived in Stripe."
                elif not body.get("recurring"):
                    note = "That is a one-time price; plans need a recurring one."
                else:
                    ok, note = True, "Active recurring price."
            else:
                token = paypal_token(cfg)
                code, body = call("GET", f"{paypal_host(cfg.environment)}/v1/billing/plans/{mapping.external_id}",
                                  headers={"Authorization": "Bearer " + token})
                if code != 200:
                    note = body.get("message") or f"PayPal returned HTTP {code}."
                elif body.get("status") != "ACTIVE":
                    note = "That PayPal plan is not active."
                else:
                    ok, note = True, "Active PayPal plan."
        except GatewayError as exc:
            note = str(exc)
    mapping.verified_at = timezone.now() if ok else None
    mapping.verification_note = note[:200]
    mapping.save(update_fields=["verified_at", "verification_note"])
    return ok


def verified_mapping(plan, provider):
    from tracker.models import PlanProviderMapping
    return PlanProviderMapping.objects.filter(plan=plan, provider=provider, verified_at__isnull=False).first()


def sellable_providers(plan):
    """Gateways through which this plan can be bought right now."""
    return [p for p in PROVIDERS if checkout_ready(p) and verified_mapping(plan, p)]


def start_checkout(provider, user, plan, success_url, cancel_url):
    """The URL on the gateway's own page where the user pays."""
    mapping = verified_mapping(plan, provider)
    if mapping is None or not checkout_ready(provider):
        raise GatewayError("This plan cannot be bought that way right now.")
    cfg = effective(provider)
    if provider == "stripe":
        form = {"mode": "subscription", "line_items[0][price]": mapping.external_id, "line_items[0][quantity]": "1",
                "client_reference_id": str(user.pk), "success_url": success_url, "cancel_url": cancel_url,
                "metadata[plan_slug]": plan.slug, "subscription_data[metadata][plan_slug]": plan.slug}
        if user.email:
            form["customer_email"] = user.email
        code, body = call("POST", STRIPE_API + "/checkout/sessions", headers=_stripe_headers(cfg), form=form)
        if code != 200 or not body.get("url"):
            raise GatewayError((body.get("error") or {}).get("message") or f"Stripe returned HTTP {code}.")
        return body["url"]
    token = paypal_token(cfg)
    code, body = call("POST", paypal_host(cfg.environment) + "/v1/billing/subscriptions",
                      headers={"Authorization": "Bearer " + token},
                      json_body={"plan_id": mapping.external_id, "custom_id": str(user.pk),
                                 "application_context": {"return_url": success_url, "cancel_url": cancel_url,
                                                         "user_action": "SUBSCRIBE_NOW"}})
    link = next((x.get("href") for x in body.get("links", []) if x.get("rel") == "approve"), "")
    if code not in (200, 201) or not link.startswith("https://"):
        raise GatewayError(body.get("message") or f"PayPal returned HTTP {code}.")
    return link


def verify_paypal_webhook(headers, body_text):
    """True when PayPal itself confirms this delivery is genuine."""
    cfg = effective("paypal")
    if not (cfg.client_id and cfg.webhook_id):
        return False
    try:
        token = paypal_token(cfg)
        code, answer = call("POST", paypal_host(cfg.environment) + "/v1/notifications/verify-webhook-signature",
                            headers={"Authorization": "Bearer " + token},
                            json_body={"auth_algo": headers.get("Paypal-Auth-Algo", ""),
                                       "cert_url": headers.get("Paypal-Cert-Url", ""),
                                       "transmission_id": headers.get("Paypal-Transmission-Id", ""),
                                       "transmission_sig": headers.get("Paypal-Transmission-Sig", ""),
                                       "transmission_time": headers.get("Paypal-Transmission-Time", ""),
                                       "webhook_id": cfg.webhook_id, "webhook_event": json.loads(body_text)})
    except (GatewayError, ValueError):
        return False
    return code == 200 and answer.get("verification_status") == "SUCCESS"
