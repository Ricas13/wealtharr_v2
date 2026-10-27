"""Recompute every audit hash and fail if any event was changed, removed or reordered."""
from django.core.management.base import BaseCommand, CommandError

from tenancy.audit import GENESIS, compute_hash
from tenancy.models import AuditChain, AuditEvent


class Command(BaseCommand):
    help = "Verify the audit log hash chains."

    def handle(self, *args, **options):
        problems, checked = [], 0
        keys = set(AuditEvent.objects.values_list("chain", flat=True)) | set(
            AuditChain.objects.values_list("key", flat=True))
        for key in sorted(keys):
            previous = GENESIS
            last = None
            for event in AuditEvent.objects.filter(chain=key).order_by("id"):
                checked += 1
                if event.prev_hash != previous:
                    problems.append("%s event %s: chain is broken (an earlier event is missing or changed)" % (key, event.pk))
                if compute_hash(event.prev_hash, event) != event.hash:
                    problems.append("%s event %s: contents do not match their hash" % (key, event.pk))
                previous, last = event.hash, event
            tip = AuditChain.objects.filter(key=key).first()
            if tip is not None and tip.last_hash != previous:
                problems.append("%s: the latest event does not match the chain tip (an event was removed)" % key)
            if tip is None and last is not None:
                problems.append("%s: events exist without a chain record" % key)
        if problems:
            raise CommandError("\n".join(problems))
        self.stdout.write("Audit log verified: %d events across %d chains." % (checked, len(keys)))
