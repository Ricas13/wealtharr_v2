"""Optional authenticator factor, encrypted using existing installation secret."""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0014_notification_delivery_category"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="OwnerTOTP",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False, auto_created=True, verbose_name="ID")),
                ("secret_ciphertext", models.TextField()),
                ("enabled_at", models.DateTimeField()),
                ("last_verified_step", models.BigIntegerField(default=-1)),
                ("failed_attempts", models.PositiveSmallIntegerField(default=0)),
                ("locked_until", models.DateTimeField(blank=True, null=True)),
                ("owner", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="totp_settings", to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
