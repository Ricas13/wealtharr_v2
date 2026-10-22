"""Delete expired shared rate-limiter buckets; used by the hourly worker."""
from django.core.management.base import BaseCommand
from django.utils import timezone

from tracker.models import RateLimitBucket


class Command(BaseCommand):
    help = "Prune expired database-backed login and public comparison rate-limit buckets"

    def handle(self, *args, **options):
        deleted, _ = RateLimitBucket.objects.filter(expires_at__lte=timezone.now()).delete()
        self.stdout.write("Rate-limit buckets pruned: %s" % deleted)
