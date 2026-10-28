"""The one way to write the audit log.

``record()`` appends an event to a hash chain (one chain per user whose data the event is
about, one for the platform). Each event's hash covers the previous hash, so editing or removing any row
breaks every hash after it, which ``manage.py verify_audit_chain`` detects.

It raises when the write fails: a privileged action that cannot be recorded must not
happen. It runs inside the caller's transaction, so an action that rolls back leaves no
event behind. Never put amounts, prices, tokens, secrets or message bodies in metadata.
"""
import hashlib
import json

from django.db import IntegrityError, transaction

from .models import AuditChain, AuditEvent

GENESIS = "0" * 64
FORBIDDEN_KEY_PARTS = ("password", "secret", "token", "ciphertext", "api_key", "apikey", "webhook", "totp")


def _check_metadata(metadata):
    try:
        json.dumps(metadata, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("Audit metadata must be plain JSON: %s" % exc)

    def walk(value):
        if isinstance(value, dict):
            for key, inner in value.items():
                lowered = str(key).lower()
                if any(part in lowered for part in FORBIDDEN_KEY_PARTS):
                    raise ValueError("Audit metadata must not carry %r" % key)
                walk(inner)
        elif isinstance(value, (list, tuple)):
            for inner in value:
                walk(inner)

    walk(metadata)


_UNSET = object()


def chain_key(subject_id):
    return "user:%d" % subject_id if subject_id else "platform"


def compute_hash(prev_hash, event):
    fields = [prev_hash, event.occurred_at.isoformat(), event.chain, event.actor_id, event.organization_id,
              event.action, event.target_type, event.target_id, event.metadata, event.ip, event.request_id]
    if event.subject_id is not None:  # events written before subjects existed hash exactly as before
        fields.append(event.subject_id)
    payload = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _request_details(request):
    from tracker.client_ip import client_ip
    return (client_ip(request) or None), request.META.get("HTTP_X_REQUEST_ID", "")[:64]


def record(action, *, actor=None, subject=_UNSET, target=None, metadata=None, request=None,
           ip=None, request_id=""):
    """Append one event and return it.

    ``subject`` is the user the event is about; when omitted it is the actor (or the signed-in
    user of ``request``). Pass ``subject=None`` for a platform-level event. ``target`` is a model
    instance or a (type, id) pair.
    """
    metadata = dict(metadata or {})
    _check_metadata(metadata)
    if request is not None:
        found_ip, found_request_id = _request_details(request)
        ip = ip or found_ip
        request_id = request_id or found_request_id
        if actor is None and getattr(request.user, "is_authenticated", False):
            actor = request.user
    if isinstance(target, tuple):
        target_type, target_id = target
    elif target is not None:
        target_type, target_id = type(target).__name__, target.pk
    else:
        target_type, target_id = "", ""
    if subject is _UNSET:
        subject = actor
    subject_id = getattr(subject, "pk", subject)
    key = chain_key(subject_id)
    with transaction.atomic():
        try:
            with transaction.atomic():
                AuditChain.objects.get_or_create(key=key, defaults={"last_hash": GENESIS})
        except IntegrityError:  # another request created the chain first
            pass
        chain = AuditChain.objects.select_for_update().get(key=key)
        event = AuditEvent(
            chain=key, actor_id=getattr(actor, "pk", None),
            actor_label=(getattr(actor, "get_username", lambda: "")() or "")[:150] if actor else "",
            subject_id=subject_id, action=action[:80],
            target_type=str(target_type)[:60], target_id=str(target_id)[:64],
            metadata=metadata, ip=ip, request_id=request_id, prev_hash=chain.last_hash)
        event.hash = compute_hash(chain.last_hash, event)
        event.save()
        chain.last_hash, chain.last_event_id = event.hash, event.pk
        chain.save(update_fields=["last_hash", "last_event_id"])
    return event
