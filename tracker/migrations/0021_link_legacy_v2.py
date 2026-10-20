"""Link provable legacy 9Sig instances to v2 IN PLACE.

All ledger entries, fills, saved strategy signals, baselines and snapshots keep
their original strategy_id and PK. Never infer broker, permissions, start date,
historical version, mapping, prices or completed trades.

Unproven instances remain visible in v2 (same StrategyInstance table) but
are explicitly marked tracking-only. Reverse only the metadata we added.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    Strategy = apps.get_model("tracker", "StrategyInstance")
    for item in Strategy.objects.select_related("account", "preset").filter(
        preset__slug="nasdaq-quarterly-target", strategy_key="",
    ).exclude(account__kind="PAPER").iterator():
        # Safe presentation-level linking for ALL old real portfolios.
        # Only account-proven identity becomes a pinned v2 key. An unknown
        # broker or ISA wrapper must not be silently reclassified.
        if not (item.account.broker == "TRADING212" and
                item.account.kind == "ISA" and item.account.country == "GB"
                and item.account.currency == "GBP" and
                item.equity_symbol == "LQQ3.L" and
                item.safe_symbol == "ERNS.L"):
            # Keep unproven records visible and blocked from executable advice.
            Strategy.objects.filter(pk=item.pk, strategy_key="").update(
                tracking_only=True
            )
            continue
        Strategy.objects.filter(pk=item.pk, strategy_key="").update(
            strategy_key="nasdaq-quarterly-target",
            platform_code="TRADING212", account_type_code="ISA",
            onboarding_mode="legacy_link",
            tracking_only=True,
            # Deliberately preserve blank strategy_version/mapping_version.
            # Pinning latest rules to historic events would falsify provenance.
        )


def backwards(apps, schema_editor):
    Strategy = apps.get_model("tracker", "StrategyInstance")
    Strategy.objects.filter(
        onboarding_mode="legacy_link", strategy_key="nasdaq-quarterly-target",
        platform_code="TRADING212", account_type_code="ISA",
        strategy_version="", mapping_version="", started_on__isnull=True,
    ).update(
        strategy_key="", platform_code="", account_type_code="",
        onboarding_mode="", tracking_only=False,
    )
    # Records that lacked verifiable account identity retain tracking-only
    # state on rollback: restoring unsafe eligibility is not reversible.


class Migration(migrations.Migration):
    dependencies = [("tracker", "0020_remove_paper_account_choice")]
    operations = [migrations.RunPython(forwards, backwards)]
