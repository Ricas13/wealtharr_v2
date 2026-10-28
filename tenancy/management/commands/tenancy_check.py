"""Check that every user's records belong together, and fingerprint every ledger.

The digests are computed from ledger rows only, so they are unchanged by anything done to
ownership or scoping: compare them before and after a deploy. Exits non-zero when a strategy
and its account have different owners, which the tenant-scoped lookups rely on.
"""
import hashlib

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Check ownership consistency and print per-strategy ledger digests."

    def add_arguments(self, parser):
        parser.add_argument("--digest", action="store_true", help="Only print the ledger digests.")

    def handle(self, *args, **options):
        from tracker.models import StrategyInstance
        from tracker.v2_ledger_core import state_digest

        if not options["digest"]:
            problems = ["StrategyInstance %s: owner differs from its account's owner" % s.pk
                        for s in StrategyInstance.objects.select_related("account")
                        if s.owner_id != s.account.owner_id]
            if problems:
                raise CommandError("\n".join(problems))
            self.stdout.write("Ownership is consistent.")
        total = hashlib.sha256()
        for strategy in StrategyInstance.objects.order_by("pk"):
            digest = state_digest(strategy)
            total.update(("%s:%s\n" % (strategy.pk, digest)).encode())
            self.stdout.write("strategy %s %s" % (strategy.pk, digest))
        self.stdout.write("all %s" % total.hexdigest())
