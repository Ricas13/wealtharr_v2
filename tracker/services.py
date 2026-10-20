"""Exact ledger maths in Decimal; no guessed market prices or fake returns."""
import base64
import hashlib
import json
import re
from collections import defaultdict
from decimal import Decimal
from urllib.request import Request, urlopen
from urllib.error import URLError
from cryptography.fernet import Fernet
from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import LedgerEntry, NotificationDelivery, NotificationPreference, PriceMark, SystemIntegration

ZERO = Decimal("0")
PENNY = Decimal("0.01")

def money(value):
    return value.quantize(PENNY)

def ledger_summary(strategy):
    cash = ZERO
    net_contributed = ZERO
    positions = defaultdict(lambda: ZERO)
    # Phase 3 append-only corrections are projected here, never by mutating
    # the original source entry or double-counting an obsolete version.
    from .v2_ledger_core import effective_entries
    for e in effective_entries(strategy):
        if e.kind == LedgerEntry.DEPOSIT:
            cash += e.amount
            net_contributed += e.amount
        elif e.kind == LedgerEntry.WITHDRAW:
            cash -= e.amount
            net_contributed -= e.amount
        elif e.kind in (LedgerEntry.DIVIDEND, LedgerEntry.INTEREST):
            cash += e.amount
        elif e.kind == LedgerEntry.FEE:
            cash -= e.amount
        elif e.kind == LedgerEntry.BUY:
            cash -= money(e.quantity * e.unit_price) + e.fee
            positions[e.symbol] += e.quantity
        elif e.kind == LedgerEntry.SELL:
            cash += money(e.quantity * e.unit_price) - e.fee
            positions[e.symbol] -= e.quantity
    positions = {symbol: qty for symbol, qty in positions.items() if qty != ZERO}
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
        last = locked.ledger.order_by("-occurred_on", "-pk").first()
        if last and entry.occurred_on < last.occurred_on:
            raise ValidationError("Enter transactions in chronological order. Earlier dates would invalidate later balances.")
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
        entry.strategy = locked
        entry.save()
    return entry

def consolidated(owner, *, include_paper=False):
    results = []
    contributed = ZERO
    total = ZERO
    missing = []
    for s in owner.strategies.select_related("account", "preset").all():
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

def encrypt_secret(raw):
    secret = settings.SECRET_KEY.encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(key).encrypt(raw.encode("utf-8")).decode("ascii")

def decrypt_secret(encoded):
    if not encoded:
        return ""
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest())
    return Fernet(key).decrypt(encoded.encode("ascii")).decode("utf-8")

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
        from urllib.parse import urlsplit
        url = decrypt_secret(cfg.discord_url_ciphertext)
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or
                parsed.hostname not in ("discord.com", "discordapp.com") or
                not re.fullmatch(r"/api/webhooks/[0-9]+/[A-Za-z0-9_-]+", parsed.path)):
            raise ValueError("Discord webhook not configured")
        payload = json.dumps({
            "content": (subject + "\n" + message)[:1900],
            "allowed_mentions": {"parse": []}
        }).encode("utf-8")
        request = Request(url, data=payload,
                          headers={"Content-Type": "application/json",
                                   "User-Agent": "Wealtharr/2"}, method="POST")
        with urlopen(request, timeout=10) as response:
            if response.status not in (200, 204):
                raise ValueError("Discord rejected notification")
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
    channels = channels if channels is not None else ("email", "discord")
    outcomes = {}
    for channel in channels:
        if channel not in ("email", "discord"):
            outcomes[channel] = "unsupported"
            continue
        if not (prefs.email_enabled if channel == "email" else prefs.discord_enabled):
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
        record.save(update_fields=["status", "error", "next_retry_at"])
    return outcomes
