"""Atomic rate-limit counters shared by Gunicorn workers through PostgreSQL.

The first failed attempt opens a bounded window. Count and expiry updates are
serialized by a row-level lock; processes cannot evade a shared rate limit.
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import RateLimitBucket


def attempts(key):
    return (RateLimitBucket.objects.filter(
        key=key, expires_at__gt=timezone.now(),
    ).values_list("count", flat=True).first() or 0)


def increment(key, window_seconds):
    if window_seconds <= 0:
        raise ValueError("Window must be positive.")
    with transaction.atomic():
        now = timezone.now()
        row, _ = RateLimitBucket.objects.select_for_update().get_or_create(
            key=key, defaults={"count": 0, "expires_at": now + timedelta(seconds=window_seconds)}
        )
        if row.expires_at <= now:
            row.count = 0
            row.expires_at = now + timedelta(seconds=window_seconds)
        row.count += 1
        row.save(update_fields=["count", "expires_at"])
        return row.count


def clear(key):
    RateLimitBucket.objects.filter(key=key).delete()
