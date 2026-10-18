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
            # All included positions must have *automated*, GBP-verified closes;
            # a stale manual mark cannot silently become a daily market snapshot.
            observed_days = []
            valid = True
            for item in report["items"]:
                for symbol, qty in item["positions"].items():
                    if qty <= 0:
                        continue
                    mark = item["marks"].get(symbol)
                    if (not mark or mark.source != "yahoo" or
                            mark.as_of < timezone.localdate() - timedelta(days=8)):
                        valid = False
                        break
                    observed_days.append(mark.as_of)
                if not valid:
                    break
            if not valid or not observed_days:
                continue
            date_to_record = min(observed_days)
            # Do not combine shares purchased AFTER a quote with an old quote.
            if (max(observed_days) - date_to_record).days > 1:
                continue
            if any(item["strategy"].ledger.filter(occurred_on__gt=date_to_record).exists()
                   for item in report["items"]):
                continue
            WealthSnapshot.objects.update_or_create(
                owner=owner, as_of=date_to_record,
                defaults={"total_value": report["total"],
                          "net_contributed": report["net_contributed"]},
            )
        self.stdout.write("Yahoo monitoring: %d verified, %d awaiting usable data" % (ok, fail))
