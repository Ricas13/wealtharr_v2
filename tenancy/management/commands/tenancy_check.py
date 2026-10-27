"""Verify the tenancy backfill, and fingerprint every ledger so before/after can be compared.

Run it before the migration (``--digest``) and after: the digests must be identical because
the migration never touches ledger rows. Exits non-zero when any row is unassigned or
inconsistent, so it can gate the step that makes ``organization`` NOT NULL.
"""
import hashlib

from django.core.management.base import BaseCommand, CommandError

from tenancy.models import Membership


class Command(BaseCommand):
    help = "Check organization assignment and print per-strategy ledger digests."

    def add_arguments(self, parser):
        parser.add_argument("--digest", action="store_true", help="Only print the ledger digests.")

    def handle(self, *args, **options):
        from tracker.models import Account, NotificationDelivery, StrategyInstance, WealthSnapshot
        from tracker.v2_ledger_core import state_digest

        if not options["digest"]:
            problems = []
            for model in (Account, StrategyInstance, WealthSnapshot, NotificationDelivery):
                rows = model.objects.all()
                unassigned = rows.filter(organization__isnull=True).count()
                if unassigned:
                    problems.append("%s: %d rows have no organization" % (model.__name__, unassigned))
                for row in rows.exclude(organization__isnull=True).select_related("organization"):
                    if not Membership.objects.filter(user_id=row.owner_id, organization_id=row.organization_id).exists():
                        problems.append("%s %s: owner is not a member of its organization" % (model.__name__, row.pk))
            for strategy in StrategyInstance.objects.select_related("account"):
                if strategy.organization_id != strategy.account.organization_id:
                    problems.append("StrategyInstance %s: organization differs from its account" % strategy.pk)
            if problems:
                raise CommandError("\n".join(problems))
            self.stdout.write("Tenancy assignment is complete and consistent.")
        total = hashlib.sha256()
        for strategy in StrategyInstance.objects.order_by("pk"):
            digest = state_digest(strategy)
            total.update(("%s:%s\n" % (strategy.pk, digest)).encode())
            self.stdout.write("strategy %s %s" % (strategy.pk, digest))
        self.stdout.write("all %s" % total.hexdigest())
