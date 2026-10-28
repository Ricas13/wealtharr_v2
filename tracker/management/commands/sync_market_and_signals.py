"""Check Yahoo quotes and review every live strategy; safe for an hourly scheduler."""
from django.core.management.base import BaseCommand
from django.utils import timezone
from tenancy.context import per_tenant
from tracker.market_data import QUOTE_MAX_AGE
from tracker.models import MarketObservation, StrategyInstance, StrategyValueSnapshot
from tracker.market_data import sync_ticker
from tracker.services import ledger_summary
from tracker.instrument_mapping import for_strategy
from tracker.review_autofinish import auto_finish_reviews
from tracker.strategies.registry import module_for, plugin_keys


def save_verified_strategy_snapshot(strategy):
    """Capture source-verified quote-day growth without inventing a historical fill."""
    # No snapshot for a strategy that isn't live or has no mapped instruments.
    if strategy.tracking_only or for_strategy(strategy) is None:
        return None
    summary = ledger_summary(strategy)
    if summary["value"] is None:
        return None
    if not summary["positions"]:
        # Unlike fixed-weight strategies, a trend strategy may deliberately
        # hold 100% cash. Its owner-ledger value needs no market quote and
        # the existing History verifier can independently certify it.
        plugin = module_for(strategy)
        if not (plugin and plugin.allows_cash_only and summary["cash"] > 0
                and strategy.ledger.exists()):
            return None
        as_of = timezone.localdate()
        StrategyValueSnapshot.objects.update_or_create(
            strategy=strategy, as_of=as_of,
            defaults={"total_value": summary["cash"],
                      "net_contributed": summary["net_contributed"]},
        )
        return as_of
    dates = []
    for symbol, qty in summary["positions"].items():
        if qty <= 0:
            continue
        mark = summary["marks"].get(symbol)
        if (mark is None or mark.source != "yahoo" or
                mark.as_of < timezone.localdate() - QUOTE_MAX_AGE or
                not MarketObservation.objects.filter(symbol=symbol, as_of=mark.as_of,
                    currency=strategy.account.currency, price=mark.price).exists()):
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
        from tenancy.billing import expire_due_subscriptions
        expire_due_subscriptions()  # paid periods that have run out
        ok = 0
        fail = 0
        owners = set()
        for strategy in per_tenant(StrategyInstance.objects.select_related("account", "preset", "owner").filter(
                market_data_enabled=True, tracking_only=False, owner__is_active=True,
                preset__slug__in=plugin_keys(), preset__active=True)):
            owners.add(strategy.owner)
            try:
                plugin = module_for(strategy)
                if plugin is None or for_strategy(strategy) is None:
                    fail += 1
                    continue
                # Each strategy refreshes its own prices and saved reviews.
                result = plugin.review(strategy)
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
        # With fresh closes in, finish any review whose fills are all recorded.
        try:
            finished = auto_finish_reviews()
        except Exception:
            finished = 0
        self.stdout.write("Yahoo monitoring: %d verified, %d awaiting usable data, %d reviews finished"
                          % (ok, fail, finished))
