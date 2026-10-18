"""Run hourly or daily from a single scheduler instance. Only sends reminders, never trades."""
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone
from tracker.models import LedgerEntry, NotificationPreference, StrategyInstance
from tracker.services import dispatch

class Command(BaseCommand):
    help = "Send idempotent email/Discord reminders for missing monthly contribution targets"

    def handle(self, *args, **options):
        today = timezone.localdate()
        count = 0
        for strategy in StrategyInstance.objects.select_related("owner", "account").filter(
                contribution_alerts=True, monthly_target__gt=0):
            pref, _ = NotificationPreference.objects.get_or_create(owner=strategy.owner)
            if not pref.monthly_reminders:
                continue
            deposits = strategy.ledger.filter(kind=LedgerEntry.DEPOSIT,
                                               occurred_on__year=today.year,
                                               occurred_on__month=today.month)
            amount = sum((e.amount for e in deposits), Decimal("0"))
            if amount >= strategy.monthly_target:
                continue
            key = "contrib:%s-%02d:strategy:%s" % (today.year, today.month, strategy.pk)
            subject = "Wealtharr — monthly contribution reminder"
            content = ("%s (%s): this month's deposits £%s / planned £%s.\n"
                       "Record a deposit in Wealtharr when you make it." %
                       (strategy.name, strategy.account.name, amount, strategy.monthly_target))
            result = dispatch(strategy.owner, key, subject, content)
            count += sum(1 for status in result.values() if status == "sent")
        self.stdout.write("Notifications sent: %d" % count)
