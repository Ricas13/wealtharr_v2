"""Give every existing user a personal organization and stamp their rows with it.

Idempotent: running it again changes nothing. The schema step is the previous migration;
the NOT NULL constraint comes later, once ``manage.py tenancy_check`` is clean.
"""
from django.conf import settings
from django.db import migrations
from django.db.models import F

TENANT_MODELS = ("Account", "StrategyInstance", "WealthSnapshot", "NotificationDelivery")


def backfill(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    Organization = apps.get_model("tenancy", "Organization")
    Membership = apps.get_model("tenancy", "Membership")
    for user in User.objects.all().order_by("pk"):
        organization = Organization.objects.filter(personal_owner=user).first()
        if organization is None:
            organization = Organization.objects.create(
                name=(user.get_username() or "Account")[:100], personal_owner=user)
        Membership.objects.get_or_create(
            user=user, organization=organization, defaults={"role": "owner"})
        for name in TENANT_MODELS:
            apps.get_model("tracker", name).objects.filter(
                owner=user, organization__isnull=True).update(organization=organization)
    # A strategy follows its account: should the two ever differ, the account wins.
    Strategy = apps.get_model("tracker", "StrategyInstance")
    for strategy in Strategy.objects.select_related("account").exclude(
            organization_id=F("account__organization_id")):
        strategy.organization_id = strategy.account.organization_id
        strategy.save(update_fields=["organization"])


class Migration(migrations.Migration):
    dependencies = [("tracker", "0036_organization_columns"), ("tenancy", "0001_initial")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
