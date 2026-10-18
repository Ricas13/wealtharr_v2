"""Check Yahoo quotes and review 9Sig portfolios; safe for an hourly scheduler."""
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from tracker.models import PriceMark, StrategyInstance, WealthSnapshot
from tracker.market_signals import review_strategy
from tracker.services import consolidated

class Command(BaseCommand):
    help = "Synchronise GBP Yahoo quotes, capture verified daily valuations and issue advisory notices"

    def handle(self, *args, **options):
        ok = 0
        fail = 0
        owners = set()
        for strategy in StrategyInstance.objects.select_related("account", "preset", "owner").filter(
                market_data_enabled=True, preset__slug="nasdaq-quarterly-target", preset__active=True):
            owners.add(strategy.owner)
            try:
                result = review_strategy(strategy)
                if result["status"] == "ok":
                    ok += 1
                else:
                    fail += 1
            except Exception:
                fail += 1
        for owner in owners:
            report = consolidated(owner)
            if report["total"] is None or not report["items"]:
                continue
            fresh = True
            for item in report["items"]:
                for symbol in item["positions"]:
                    mark = item["marks"].get(symbol)
                    if not mark or mark.as_of < timezone.localdate() - timedelta(days=8):
                        fresh = False
            if fresh:
                WealthSnapshot.objects.update_or_create(
                    owner=owner, as_of=timezone.localdate(),
                    defaults={"total_value": report["total"],
                              "net_contributed": report["net_contributed"]},
                )
        self.stdout.write("Yahoo monitoring: %d verified, %d awaiting usable data" % (ok, fail))
