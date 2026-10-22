import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0026_more_account_types"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PriceRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("symbol", models.CharField(max_length=24)),
                ("as_of", models.DateField()),
                ("currency", models.CharField(max_length=3)),
                ("stored_price", models.DecimalField(decimal_places=8, max_digits=22)),
                ("yahoo_price", models.DecimalField(decimal_places=8, max_digits=22)),
                ("yahoo_raw_close", models.DecimalField(blank=True, decimal_places=8, max_digits=22, null=True)),
                ("detected_at", models.DateTimeField(auto_now_add=True)),
                ("accepted_at", models.DateTimeField(blank=True, null=True)),
                ("accepted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                                  related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["symbol", "as_of", "pk"]},
        ),
    ]
