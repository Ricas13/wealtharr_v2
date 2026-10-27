from django.db import migrations, models


def backfill(apps, schema_editor):
    """Label existing scheduled reviews with their strategy's cadence."""
    from tracker.strategies.registry import module_for_key
    Signal = apps.get_model("tracker", "StrategySignal")
    for signal in Signal.objects.filter(kind="QUARTERLY", cadence="").select_related("strategy__preset"):
        instance = signal.strategy
        plugin = module_for_key(instance.strategy_key or instance.preset.slug)
        Signal.objects.filter(pk=signal.pk).update(cadence=plugin.cadence_word if plugin else "quarterly")


class Migration(migrations.Migration):
    dependencies = [("tracker", "0034_shared_rate_limits")]
    operations = [
        migrations.AddField(
            model_name="strategysignal", name="cadence",
            field=models.CharField(blank=True, default="", max_length=12)),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
