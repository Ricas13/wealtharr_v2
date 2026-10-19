"""Optional RFC 6238-style TOTP for private Django account sessions.

Pure stdlib generator. Codes are never logged; encrypted secrets stay in the
database. This is an authentication factor, never a financial-rule input.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import timedelta
from urllib.parse import quote

from django.db import transaction
from django.utils import timezone

from .models import OwnerTOTP
from .services import decrypt_secret, encrypt_secret

DIGITS = 6
STEP_SECONDS = 30
ATTEMPTS_BEFORE_LOCK = 5
LOCK_MINUTES = 15


def generate_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _hotp(secret: str, step: int) -> str:
    raw = secret.strip().upper()
    data = base64.b32decode(raw + "=" * ((-len(raw)) % 8), casefold=True)
    digest = hmac.new(data, struct.pack(">Q", step), hashlib.sha1).digest()
    off = digest[-1] & 15
    value = struct.unpack(">I", digest[off:off + 4])[0] & 0x7fffffff
    return f"{value % (10 ** DIGITS):0{DIGITS}d}"


def current_code(secret: str, *, at_time: int | None = None) -> str:
    return _hotp(secret, int(time.time() if at_time is None else at_time) // STEP_SECONDS)


def matching_step(secret: str, code: str, *, at_time: int | None = None,
                  newer_than: int = -1) -> int | None:
    """Accept at most one step's clock skew, with constant-time code compare."""
    if not isinstance(code, str) or len(code) != DIGITS or not code.isascii() or not code.isdigit():
        return None
    step = int(time.time() if at_time is None else at_time) // STEP_SECONDS
    for candidate in (step - 1, step, step + 1):
        if candidate > newer_than and candidate >= 0 and hmac.compare_digest(
                _hotp(secret, candidate), code):
            return candidate
    return None


def provisioning_uri(username: str, secret: str) -> str:
    label = quote("Wealtharr:" + username, safe="")
    issuer = quote("Wealtharr", safe="")
    return f"otpauth://totp/{label}?secret={secret}&issuer={issuer}&digits=6&period=30"


def verify_account_code(user, code: str) -> bool:
    """Lock anti-replay and bounded guessing across sessions / processes."""
    with transaction.atomic():
        factor = OwnerTOTP.objects.select_for_update().filter(owner=user).first()
        if not factor:
            return False
        now = timezone.now()
        if factor.locked_until and factor.locked_until > now:
            return False
        try:
            secret = decrypt_secret(factor.secret_ciphertext)
            step = matching_step(secret, code, newer_than=factor.last_verified_step)
        except (ValueError, TypeError, KeyError):
            step = None
        if step is None:
            factor.failed_attempts = min(factor.failed_attempts + 1, ATTEMPTS_BEFORE_LOCK)
            if factor.failed_attempts >= ATTEMPTS_BEFORE_LOCK:
                factor.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            factor.save(update_fields=["failed_attempts", "locked_until"])
            return False
        factor.last_verified_step = step
        factor.failed_attempts = 0
        factor.locked_until = None
        factor.save(update_fields=["last_verified_step", "failed_attempts", "locked_until"])
        return True
