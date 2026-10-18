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
    for e in strategy.ledger.all().order_by("occurred_on", "pk"):
        if e.kind == LedgerEntry.DEPOSIT:
            cash += e.amount
            net_contributed += e.amount
        elif e.kind == LedgerEntry.WITHDRAW:
            cash -= e.amount
            net_contributed -= e.amount
        elif e.kind == LedgerEntry.DIVIDEND:
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

def consolidated(owner):
    results = []
    contributed = ZERO
    total = ZERO
    missing = []
    for s in owner.strategies.select_related("account", "preset").all():
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

def dispatch(owner, key, subject, message, channels=None):
    """Idempotent per owner/channel/event. Secrets never included in messages or logs."""
    prefs, _ = NotificationPreference.objects.get_or_create(owner=owner)
    cfg = SystemIntegration.current()
    channels = channels or ("email", "discord")
    outcomes = {}
    for channel in channels:
        enabled = prefs.email_enabled if channel == "email" else prefs.discord_enabled
        if not enabled:
            outcomes[channel] = "disabled"
            continue
        try:
            with transaction.atomic():
                delivery, fresh = NotificationDelivery.objects.get_or_create(
                    owner=owner, channel=channel, event_key=key,
                )
                if not fresh:
                    outcomes[channel] = delivery.status.lower()
                    continue
                # Commit the reservation before doing network IO; never duplicate sends.
            if channel == "email":
                if not cfg.smtp_host or not cfg.smtp_sender or not prefs.destination_email:
                    raise ValueError("Email needs SMTP host, sender and destination")
                conn = get_connection(
                    backend="django.core.mail.backends.smtp.EmailBackend",
                    host=cfg.smtp_host, port=cfg.smtp_port,
                    username=cfg.smtp_username or None,
                    password=decrypt_secret(cfg.smtp_password_ciphertext) or None,
                    use_tls=cfg.smtp_use_tls, timeout=10,
                )
                EmailMessage(subject, message, cfg.smtp_sender, [prefs.destination_email], connection=conn).send(fail_silently=False)
            elif channel == "discord":
                url = decrypt_secret(cfg.discord_url_ciphertext)
                if not url.startswith("https://discord.com/api/webhooks/") and not url.startswith("https://discordapp.com/api/webhooks/"):
                    raise ValueError("Discord webhook has not been configured")
                payload = json.dumps({"content": (subject + "\n" + message)[:1900]}).encode("utf-8")
                request = Request(url, data=payload, headers={"Content-Type": "application/json", "User-Agent": "Wealtharr/2"}, method="POST")
                with urlopen(request, timeout=10) as response:
                    if response.status not in (200, 204):
                        raise ValueError("Discord rejected the message")
            delivery.status = "SENT"
            outcomes[channel] = "sent"
        except Exception:
            # Hide credentials and remote webhook responses (which may contain secrets).
            delivery.status = "FAILED"
            delivery.error = "Delivery failed; verify integration and destination settings"
            outcomes[channel] = "failed"
        delivery.save(update_fields=["status", "error"])
    return outcomes
