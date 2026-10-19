"""Append-only, owner-scoped revisions for the Phase 3 ledger interface."""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0016_v2_strategy_instances"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="LedgerRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version", models.PositiveIntegerField()),
                ("operation", models.CharField(
                    choices=[
                        ("ADD", "Added"), ("EDIT", "Edited"),
                        ("DELETE", "Soft deleted"), ("RESTORE", "Restored"),
                    ], max_length=10,
                )),
                ("before", models.JSONField(blank=True, null=True)),
                ("after", models.JSONField(blank=True, null=True)),
                ("impact", models.JSONField(default=dict)),
                ("reason", models.CharField(blank=True, max_length=280)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("entry", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="revisions", to="tracker.ledgerentry"
                )),
                ("strategy", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="ledger_revisions", to="tracker.strategyinstance"
                )),
                ("actor", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    to=settings.AUTH_USER_MODEL
                )),
            ],
            options={"ordering": ["entry_id", "version"]},
        ),
        migrations.CreateModel(
            name="OpeningReconciliation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file_sha256", models.CharField(max_length=64)),
                ("accepted_rows", models.PositiveIntegerField()),
                ("reconciled_at", models.DateTimeField(auto_now_add=True)),
                ("opening", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                     related_name="reconciliation", to="tracker.instanceopeningsnapshot")),
                ("reconciled_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                     to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="ledgerrevision",
            constraint=models.UniqueConstraint(
                fields=("entry", "version"), name="unique_ledger_revision"
            ),
        ),
    ]
