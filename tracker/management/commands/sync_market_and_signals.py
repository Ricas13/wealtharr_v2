"""Check Yahoo quotes and review 9Sig portfolios; safe for an hourly scheduler."""
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from tracker.models import MarketObservation, StrategyInstance, StrategyValueSnapshot, WealthSnapshot
from tracker.market_signals import review_strategy, sync_ticker
from tracker.services import consolidated, ledger_summary
from tracker.instrument_mapping import for_strategy
from tracker.models import OpeningReconciliation, InstanceOpeningSnapshot


def save_verified_strategy_snapshot(strategy):
    """Capture source-verified quote-day growth without inventing a historical fill."""
    # Never write a verified snapshot for an owner-reported or revised ledger
    # before the exact account and historical cashflows are reconciled.
    if (strategy.tracking_only or for_strategy(strategy) is None or
            InstanceOpeningSnapshot.objects.filter(strategy=strategy,
                reconciliation__isnull=True).exists()):
        return None
    summary = ledger_summary(strategy)
    if summary["value"] is None or not summary["positions"]:
        return None
    dates = []
    for symbol, qty in summary["positions"].items():
        if qty <= 0:
            continue
        mark = summary["marks"].get(symbol)
        if (mark is None or mark.source != "yahoo" or
                mark.as_of < timezone.localdate() - timedelta(days=8) or
                not MarketObservation.objects.filter(symbol=symbol, as_of=mark.as_of,
                    currency="GBP", price=mark.price).exists()):
            return None
        dates.append(mark.as_of)
    if not dates:
        return None
    date_to_record = min(dates)
    if any(day != date_to_record for day in dates):
        return None
    if strategy.ledger.filter(occurred_on__gt=date_to_record).exists():
        return None
    StrategyValueSnapshot.objects.update_or_create(
        strategy=strategy, as_of=date_to_record,
        defaults={"total_value": summary["value"],
                  "net_contributed": summary["net_contributed"]},
    )
    return date_to_record


class Command(BaseCommand):
    help = "Synchronise GBP Yahoo quotes, capture verified daily valuations and issue advisory notices"

    def handle(self, *args, **options):
        ok = 0
        fail = 0
        owners = set()
        for strategy in StrategyInstance.objects.select_related("account", "preset", "owner").filter(
                market_data_enabled=True, tracking_only=False,
                preset__slug="nasdaq-quarterly-target", preset__active=True):
            owners.add(strategy.owner)
            try:
                if (for_strategy(strategy) is None or
                        InstanceOpeningSnapshot.objects.filter(strategy=strategy,
                            reconciliation__isnull=True).exists()):
                    fail += 1
                    continue
                result = review_strategy(strategy)
                save_verified_strategy_snapshot(strategy)
                if result["status"] == "ok":
                    ok += 1
                else:
                    fail += 1
            except Exception:
                fail += 1
        # Index comparison lines only (USD price change); never an input to advice.
        if owners:
            for symbol in ("QQQ", "SPY"):
                try:
                    sync_ticker(symbol)
                except Exception:
                    pass
        for owner in owners:
            # One unverified/changed component blocks an aggregated GBP NAV.
            # Omit neither a real owner strategy nor its cash silently.
            if (StrategyInstance.objects.filter(owner=owner,
                    tracking_only=True).exclude(account__kind="PAPER").exists() or
                    InstanceOpeningSnapshot.objects.filter(strategy__owner=owner,
                        reconciliation__isnull=True).exists()):
                continue
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
                            mark.as_of < timezone.localdate() - timedelta(days=8) or
                            not MarketObservation.objects.filter(symbol=symbol, as_of=mark.as_of,
                                currency="GBP", price=mark.price).exists()):
                        valid = False
                        break
                    observed_days.append(mark.as_of)
                if not valid:
                    break
            if not valid or not observed_days:
                continue
            date_to_record = min(observed_days)
            # Do not combine shares purchased AFTER a quote with an old quote.
            if any(day != date_to_record for day in observed_days):
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
