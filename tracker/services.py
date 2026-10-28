"""Exact ledger maths in Decimal; no guessed market prices or fake returns."""
import base64
import hashlib
import logging
import re
from collections import defaultdict
from decimal import Decimal
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import LedgerEntry, NotificationDelivery, NotificationPreference, PriceMark, SystemIntegration
from .tenant_scope import strategies_of

LOG = logging.getLogger(__name__)
ZERO = Decimal("0")
PENNY = Decimal("0.01")

def money(value):
    return value.quantize(PENNY)

SHARE_PLACES = Decimal("0.00000001")


def split_quantity(held, ratio):
    """Shares after a split or consolidation (8 places, like every share quantity)."""
    return (held * ratio).quantize(SHARE_PLACES)


def replay(entries):
    """Cash, net contributions and positions after the given ledger entries.

    Reads back a saved ledger without re-validating it; the single fold lives in
    v2_ledger_core (where the validating replay also uses it).
    """
    from .v2_ledger_core import replay_trusted
    return replay_trusted(entries)


def ledger_summary(strategy):
    # Phase 3 append-only corrections are projected here, never by mutating
    # the original source entry or double-counting an obsolete version.
    from .v2_ledger_core import effective_entries
    cash, net_contributed, positions = replay(effective_entries(strategy))
    prices = {p.symbol: p for p in strategy.prices.all()}
    missing = sorted(symbol for symbol, qty in positions.items() if qty > ZERO and symbol not in prices)
    stock_value = sum((qty * prices[symbol].price for symbol, qty in positions.items() if symbol in prices), ZERO)
    total = None if missing else money(cash + stock_value)
    return {
        "cash": money(cash), "net_contributed": money(net_contributed),
        "positions": positions, "marks": prices, "missing": missing,
        "value": total, "profit": None if total is None else money(total - net_contributed),
    }

def validate_and_record(strategy, entry):
    """Append-only, strictly account-funded trades. No negative positions or cash."""
    with transaction.atomic():
        locked = type(strategy).objects.select_for_update().get(pk=strategy.pk, owner=strategy.owner)
        summary = ledger_summary(locked)
        qty = summary["positions"].get(entry.symbol, ZERO)
        cash = summary["cash"]
        if entry.kind == LedgerEntry.WITHDRAW and entry.amount > cash:
            raise ValidationError("This withdrawal exceeds available strategy cash.")
        if entry.kind == LedgerEntry.FEE and entry.amount > cash:
            raise ValidationError("This fee exceeds available strategy cash.")
        if entry.kind == LedgerEntry.BUY and money(entry.quantity * entry.unit_price) + entry.fee > cash:
            raise ValidationError("Not enough strategy cash. Record a deposit first.")
        if entry.kind == LedgerEntry.SELL and entry.quantity > qty:
            raise ValidationError("Cannot sell more units than this strategy owns.")
        if entry.kind == LedgerEntry.SELL and money(entry.quantity * entry.unit_price) < entry.fee:
            raise ValidationError("Selling fees exceed the sale proceeds.")
        if entry.kind == LedgerEntry.SPLIT and (qty <= ZERO or entry.quantity <= ZERO or entry.quantity == 1):
            raise ValidationError("A split needs shares held and a ratio other than 1.")
        # Back-dated entries are fine as long as every balance from that date
        # on stays valid (no negative cash or shares at any point). This replay
        # is the check: the strict v2 replay RAISES on any negative intermediate
        # balance, so keep it even though its result is unused. The new entry
        # gets the highest pk (as it will once saved), so among same-day entries
        # it sorts last here exactly as it will in every later replay.
        from .v2_ledger_core import EffectiveEntry, effective_entries, replay
        replay(list(effective_entries(locked)) + [EffectiveEntry(
            pk=2**62, strategy_id=locked.pk, occurred_on=entry.occurred_on,
            kind=entry.kind, symbol=entry.symbol or "", quantity=entry.quantity or ZERO,
            unit_price=entry.unit_price or ZERO, amount=entry.amount or ZERO,
            fee=entry.fee or ZERO, note="", created_at=None)])
        entry.strategy = locked
        entry.save()
    return entry

def consolidated(owner, *, include_paper=False):
    results = []
    contributed = ZERO
    total = ZERO
    missing = []
    for s in strategies_of(owner).select_related("account", "preset"):
        if s.account.kind == "PAPER" and not include_paper:
            continue
        summary = ledger_summary(s)
        results.append({"strategy": s, **summary})
        contributed += summary["net_contributed"]
        if summary["value"] is None:
            missing.extend(str(s.name) + ": " + symbol for symbol in summary["missing"])
        else:
            total += summary["value"]
    complete = not missing
    return {
        "items": results, "total": money(total) if complete else None,
        "net_contributed": money(contributed),
        "gain": money(total - contributed) if complete else None,
        "missing": missing,
    }

def _fernet_for_secret(secret):
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_secret(raw):
    return _fernet_for_secret(settings.DATA_ENCRYPTION_KEY or settings.SECRET_KEY).encrypt(
        raw.encode("utf-8")).decode("ascii")


def decrypt_secret(encoded):
    if not encoded:
        return ""
    # During migration legacy data stays readable; re-encrypt before rotating
    # Django's signing key.
    for key in (settings.DATA_ENCRYPTION_KEY, settings.SECRET_KEY):
        if key:
            try:
                return _fernet_for_secret(key).decrypt(encoded.encode("ascii")).decode("utf-8")
            except InvalidToken:
                pass
    raise InvalidToken("No configured key could decrypt the saved credential.")

def _send_message(channel, owner, cfg, prefs, subject, message):
    """One outbound attempt. Secrets remain on the server and out of logs."""
    if channel == "email":
        if not cfg.smtp_host or not cfg.smtp_sender or not prefs.destination_email:
            raise ValueError("Email not configured")
        connection = get_connection(
            backend="django.core.mail.backends.smtp.EmailBackend",
            host=cfg.smtp_host, port=cfg.smtp_port,
            username=cfg.smtp_username or None,
            password=decrypt_secret(cfg.smtp_password_ciphertext) or None,
            use_tls=cfg.smtp_use_tls, timeout=10,
        )
        EmailMessage(subject, message, cfg.smtp_sender,
                     [prefs.destination_email], connection=connection).send(fail_silently=False)
    elif channel == "discord":
        # A private DM from the site's bot to the owner's connected account.
        from .discord_dm import send_dm
        if not prefs.discord_user_id:
            raise ValueError("Discord not connected")
        send_dm(cfg, prefs.discord_user_id, subject + "\n" + message)
    elif channel == "discord_webhook":
        # The person's own channel webhook (their server, not the site's).
        from .discord_dm import post_webhook
        post_webhook(decrypt_secret(prefs.discord_webhook_ciphertext), subject + "\n" + message)
    elif channel == "telegram":
        # A message from the site's Telegram bot to the owner's connected chat.
        from .telegram_bot import send
        if not prefs.telegram_chat_id:
            raise ValueError("Telegram not connected")
        send(decrypt_secret(cfg.telegram_bot_token_ciphertext), prefs.telegram_chat_id, subject + "\n" + message)
    else:
        raise ValueError("Unsupported notification channel")


def dispatch(owner, key, subject, message, channels=None, *, category="", strategy=None):
    """Persist and deliver each alert with bounded, retryable, per-channel status.

    A network acknowledgement can be lost after the remote service accepts a
    message. Like other at-least-once delivery systems, a retry could duplicate
    an alert in that rare case. Trade orders are never sent.
    """
    from datetime import timedelta
    if strategy is not None and strategy.owner_id != owner.pk:
        raise ValueError("Strategy does not belong to notification owner")
    prefs, _ = NotificationPreference.objects.get_or_create(owner=owner)
    cfg = SystemIntegration.current()
    from .notification_routing import DELIVERY_CHANNELS
    channels = channels if channels is not None else DELIVERY_CHANNELS
    outcomes = {}
    for channel in channels:
        if channel not in DELIVERY_CHANNELS:
            outcomes[channel] = "unsupported"
            continue
        if not getattr(prefs, channel + "_enabled"):
            outcomes[channel] = "disabled"
            continue
        now = timezone.now()
        try:
            with transaction.atomic():
                record, created = NotificationDelivery.objects.select_for_update().get_or_create(
                    owner=owner, channel=channel, event_key=key,
                    defaults={"subject": subject[:200], "message": message,
                              "category": category, "strategy": strategy},
                )
                if (record.strategy_id != (strategy.pk if strategy is not None else None)):
                    outcomes[channel] = "failed"
                    continue
                if record.category and category and record.category != category:
                    outcomes[channel] = "failed"
                    continue
                if record.status == "SENT":
                    outcomes[channel] = "sent"
                    continue
                if record.status == "CANCELLED":
                    # Owner explicitly opted out after the notice was queued.
                    # Re-enabling a category never resurrects stale advice.
                    outcomes[channel] = "cancelled"
                    continue
                if record.attempts >= 3:
                    outcomes[channel] = "failed"
                    continue
                if record.next_retry_at and record.next_retry_at > now:
                    outcomes[channel] = "retrying"
                    continue
                if (record.status == "SENDING" and record.last_attempt_at and
                        record.last_attempt_at > now - timedelta(minutes=15)):
                    outcomes[channel] = "pending"
                    continue
                # Freeze payload for retries; refreshed advice must not
                # silently alter a previously queued dated notice.
                if not record.subject:
                    record.subject = subject[:200]
                if not record.message:
                    record.message = message
                if category and not record.category:
                    record.category = category
                record.attempts += 1
                record.last_attempt_at = now
                record.next_retry_at = None
                record.error = ""
                record.status = "SENDING"
                record.save(update_fields=["subject", "message", "category",
                                           "attempts", "last_attempt_at",
                                           "next_retry_at", "status", "error"])
        except Exception:
            # Database failure must not trigger an untracked, duplicate send.
            outcomes[channel] = "failed"
            continue
        try:
            _send_message(channel, owner, cfg, prefs, record.subject, record.message)
        except Exception:
            record.status = "FAILED"
            record.error = "Delivery failed; check integration and destination settings"
            record.next_retry_at = (
                timezone.now() + timedelta(minutes=5 * (3 ** (record.attempts - 1)))
                if record.attempts < 3 else None
            )
            outcomes[channel] = "retrying" if record.next_retry_at else "failed"
        else:
            record.status = "SENT"
            record.error = ""
            record.next_retry_at = None
            outcomes[channel] = "sent"
        try:
            record.save(update_fields=["status", "error", "next_retry_at"])
        except Exception:
            # The message was (or wasn't) sent, but its record can't be updated:
            # a later run finds it SENDING and retries after 15 minutes. Don't let
            # a database hiccup abort the other channels or the caller.
            LOG.exception("Could not record the delivery result for %s", key)
            outcomes[channel] = "pending"
    return outcomes
