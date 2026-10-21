"""Owner go-live: the one explicit step that ends a strategy's tracking-only state.

The owner confirms the pinned instruments are buyable in their own account.
Wealtharr then fetches Yahoo prices for the strategy and may show advisory
instructions. All other fail-closed checks (fresh prices, reconciled holdings,
unchanged versions) still apply. Nothing here places a trade.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from .core.catalogue import (
    CATALOGUE_VERSION, MappingNotAvailable, onboarding_manifest, released_version,
)
from .instrument_mapping import for_strategy
from .models import StrategyGoLive, StrategyInstance


def attestation_text(strategy):
    roles = (strategy.resolved_instruments or {}).get("roles", {})
    growth = roles.get("growth", {}).get("isin", "")
    safe = roles.get("safe", {}).get("isin", "")
    return (
        f"I checked in my own {strategy.account.broker} {strategy.account.kind} account that "
        f"{strategy.equity_symbol}{f' ({growth})' if growth else ''} and "
        f"{strategy.safe_symbol}{f' ({safe})' if safe else ''} can be bought. "
        "Wealtharr never trades for me."
    )


def setup_missing(strategy):
    """Live but never fully pinned: a linked legacy strategy that went live
    before go-live filled in the start date. The ledger can't record until
    the owner finishes setup."""
    return not (strategy.strategy_key and strategy.started_on and strategy.resolved_instruments)


def go_live(strategy, user):
    """Switch one owner strategy live, or raise ValidationError and change nothing."""
    with transaction.atomic():
        strategy = StrategyInstance.objects.select_for_update().select_related(
            "account", "preset").get(pk=strategy.pk)
        if strategy.owner_id != user.pk or strategy.account.owner_id != user.pk:
            raise ValidationError("This strategy doesn't belong to you.")
        if not strategy.tracking_only and not setup_missing(strategy):
            raise ValidationError("This strategy is already live.")
        key = strategy.strategy_key or strategy.preset.slug
        if key != strategy.preset.slug or strategy.account.currency != "GBP":
            raise ValidationError("This strategy's account doesn't match a supported mapping.")
        try:
            manifest = onboarding_manifest(key, strategy.account.broker, strategy.account.kind)
            version = released_version(key)
        except MappingNotAvailable as exc:
            raise ValidationError(f"Can't go live: {exc}")
        # Never repin a strategy already pinned to different rules or lines.
        if strategy.strategy_version and strategy.strategy_version != version:
            raise ValidationError(
                "This strategy is pinned to an older rule version; it needs an explicit upgrade first.")
        if strategy.resolved_instruments and strategy.resolved_instruments != manifest:
            raise ValidationError(
                "The instrument mapping changed since this strategy was set up; it needs an explicit upgrade first.")
        # Identity fields are immutable through save(); this owner action pins
        # the blank fields of a linked legacy strategy once, going forward only.
        StrategyInstance.objects.filter(pk=strategy.pk).update(
            strategy_key=key,
            platform_code=strategy.account.broker,
            account_type_code=strategy.account.kind,
            strategy_version=version,
            mapping_version=CATALOGUE_VERSION,
            resolved_instruments=manifest,
            owner_checked_on=strategy.owner_checked_on or timezone.localdate(),
            # A linked legacy strategy has no pinned start; its first entry is it.
            started_on=strategy.started_on or (
                strategy.ledger.order_by("occurred_on").values_list("occurred_on", flat=True).first()
                or timezone.localdate()),
            tracking_only=False,
            market_data_enabled=True,
        )
        strategy.refresh_from_db()
        if for_strategy(strategy) is None:
            raise ValidationError(
                "These instruments aren't a verified trading line for this account, so it can't go live.")
        record, _ = StrategyGoLive.objects.get_or_create(strategy=strategy, defaults={
            "confirmed_by": user, "strategy_version": version,
            "mapping_version": CATALOGUE_VERSION, "attestation": attestation_text(strategy),
        })
        return record


@login_required
@require_POST
def go_live_view(request, instance_id):
    strategy = get_object_or_404(
        StrategyInstance.objects.select_related("account", "preset").filter(
            owner=request.user, account__owner=request.user,
        ).exclude(account__kind="PAPER"), pk=instance_id,
    )
    if request.POST.get("confirm") != "yes":
        messages.error(request, "Tick the confirmation box to go live.")
        return redirect("v2_instance_page", instance_id=strategy.pk, section="strategy")
    try:
        go_live(strategy, request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("v2_instance_page", instance_id=strategy.pk, section="strategy")
    if not strategy.tracking_only:
        messages.success(request, f"{strategy.name} setup is finished. You can record entries now.")
        return redirect("v2_instance_page", instance_id=strategy.pk, section="today")
    messages.success(
        request,
        f"{strategy.name} is live. Yahoo prices are fetched on the next hourly run; "
        "Today shows instructions once your holdings and prices are in.")
    return redirect("v2_instance_page", instance_id=strategy.pk, section="today")
