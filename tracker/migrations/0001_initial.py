from decimal import Decimal
from django.conf import settings
from django.db import migrations, models
from django.db.models import deletion

def seed(apps, schema_editor):
    P = apps.get_model("tracker", "StrategyPreset")
    for slug, name, risk, summary, method in [
        ("nasdaq-quarterly-target", "Nasdaq 3x Quarterly Target (9Sig-style)", "HIGH",
         "Track a quarterly-target leveraged Nasdaq strategy in a specific account.",
         "Tracking only: quarterly target calculations, 30-down signals and automatic trades are not implemented."),
        ("balanced-60-40", "Balanced 60/40", "MEDIUM", "Track a classic equity and bond portfolio.",
         "Weights are informational only; no automatic rebalance signals."),
        ("three-fund", "Three-Fund Portfolio", "MEDIUM", "Track a diversified three-fund portfolio.",
         "Select your real instruments; fund suitability is not assumed."),
        ("all-weather", "All Weather", "LOW", "Track a multi-asset All Weather style portfolio.",
         "Risk labels are approximate. Actual risk depends on your holdings."),
        ("hfea", "Leveraged Risk Parity (HFEA-style)", "HIGH", "Track a leveraged equity and Treasury approach.",
         "Significant loss risk. Automated signals are not implemented."),
    ]:
        P.objects.get_or_create(slug=slug, defaults={"name":name,"risk":risk,"summary":summary,"methodology":method})


def fk(to, related_name=None, protect=False):
    kw = {"on_delete": deletion.PROTECT if protect else deletion.CASCADE, "to": to}
    if related_name: kw["related_name"] = related_name
    return models.ForeignKey(**kw)
def dec(places=2, digits=20, default=None):
    opts = {"max_digits":digits, "decimal_places":places}
    if default is not None: opts["default"] = default
    return models.DecimalField(**opts)

class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel("StrategyPreset", [
            ("id", models.BigAutoField(primary_key=True, serialize=False)), ("slug",models.SlugField(unique=True)), ("name",models.CharField(max_length=100)),
            ("summary",models.TextField()), ("risk",models.CharField(max_length=10,choices=[("LOW","Low"),("MEDIUM","Medium"),("HIGH","High")])),
            ("methodology",models.TextField(blank=True)), ("active",models.BooleanField(default=True))
        ], options={"ordering":["risk","name"]}),
        migrations.CreateModel("SystemIntegration", [
            ("id", models.BigAutoField(primary_key=True, serialize=False)), ("smtp_host",models.CharField(max_length=255,blank=True)),
            ("smtp_port",models.PositiveIntegerField(default=587)),
            ("smtp_username",models.CharField(max_length=255,blank=True)),
            ("smtp_password_ciphertext",models.TextField(blank=True)),
            ("smtp_sender",models.EmailField(blank=True)),
            ("smtp_use_tls",models.BooleanField(default=True)),
            ("discord_url_ciphertext",models.TextField(blank=True)),
        ]),
        migrations.CreateModel("Account", [
            ("id", models.BigAutoField(primary_key=True, serialize=False)), ("name",models.CharField(max_length=90)),
            ("kind",models.CharField(max_length=12,default="ISA",choices=[
                ("ISA","Stocks & Shares ISA"),("GIA","General investment account"),
                ("SIPP","SIPP / pension"),("OTHER","Other account")])),
            ("currency",models.CharField(max_length=3,default="GBP",editable=False)),
            ("created_at",models.DateTimeField(auto_now_add=True)),
            ("owner",fk(settings.AUTH_USER_MODEL,"investment_accounts")),
        ], options={"ordering":["name"]}),
        migrations.CreateModel("StrategyInstance", [
            ("id", models.BigAutoField(primary_key=True, serialize=False)), ("name",models.CharField(max_length=100)),
            ("monthly_target",dec(2,16,Decimal("0"))),("contribution_alerts",models.BooleanField(default=True)),
            ("created_at",models.DateTimeField(auto_now_add=True)),
            ("owner",fk(settings.AUTH_USER_MODEL,"strategies")),
            ("account",fk("tracker.account","strategies",True)),("preset",fk("tracker.strategypreset",protect=True)),
        ], options={"ordering":["created_at","pk"]}),
        migrations.CreateModel("LedgerEntry", [
            ("id", models.BigAutoField(primary_key=True, serialize=False)), ("kind",models.CharField(max_length=12,choices=[
                ("DEPOSIT","Add money"),("WITHDRAW","Withdraw money"),("BUY","Buy"),("SELL","Sell"),
                ("DIVIDEND","Dividend"),("FEE","Other fee")])),
            ("occurred_on",models.DateField()),("symbol",models.CharField(max_length=24,blank=True)),
            ("quantity",dec(8,22,Decimal("0"))),("unit_price",dec(8,22,Decimal("0"))),
            ("amount",dec(2,20,Decimal("0"))),("fee",dec(2,20,Decimal("0"))),
            ("note",models.CharField(max_length=280,blank=True)),
            ("created_at",models.DateTimeField(auto_now_add=True)),
            ("strategy",fk("tracker.strategyinstance","ledger")),
        ],options={"ordering":["occurred_on","pk"]}),
        migrations.CreateModel("PriceMark", [
            ("id", models.BigAutoField(primary_key=True, serialize=False)),("symbol",models.CharField(max_length=24)),("price",dec(8,22)),
            ("as_of",models.DateField()),("strategy",fk("tracker.strategyinstance","prices")),
        ]),
        migrations.CreateModel("WealthSnapshot", [
            ("id", models.BigAutoField(primary_key=True, serialize=False)),("as_of",models.DateField()),("total_value",dec(2,24)),
            ("net_contributed",dec(2,24)),("captured_at",models.DateTimeField(auto_now_add=True)),
            ("owner",fk(settings.AUTH_USER_MODEL,"wealth_snapshots")),
        ],options={"ordering":["as_of"]}),
        migrations.CreateModel("NotificationPreference", [
            ("id", models.BigAutoField(primary_key=True, serialize=False)),("destination_email",models.EmailField(blank=True)),
            ("email_enabled",models.BooleanField(default=False)),
            ("discord_enabled",models.BooleanField(default=False)),
            ("monthly_reminders",models.BooleanField(default=True)),
            ("owner",models.OneToOneField(to=settings.AUTH_USER_MODEL,on_delete=deletion.CASCADE,related_name="notification_preference")),
        ]),
        migrations.CreateModel("NotificationDelivery", [
            ("id", models.BigAutoField(primary_key=True, serialize=False)),("channel",models.CharField(max_length=12)),("event_key",models.CharField(max_length=160)),
            ("status",models.CharField(max_length=12,default="PENDING")),
            ("created_at",models.DateTimeField(auto_now_add=True)),
            ("error",models.CharField(max_length=200,blank=True)),
            ("owner",fk(settings.AUTH_USER_MODEL)),
        ]),
        migrations.AddConstraint("account",models.UniqueConstraint(fields=("owner","name"),name="account_unique_owner_name")),
        migrations.AddConstraint("strategyinstance",models.UniqueConstraint(fields=("owner","account","name"),name="strategy_unique_account_name")),
        migrations.AddConstraint("pricemark",models.UniqueConstraint(fields=("strategy","symbol"),name="unique_strategy_mark")),
        migrations.AddConstraint("wealthsnapshot",models.UniqueConstraint(fields=("owner","as_of"),name="unique_owner_snapshot_day")),
        migrations.AddConstraint("notificationdelivery",models.UniqueConstraint(fields=("owner","channel","event_key"),name="unique_notification_event")),
        migrations.AddIndex("ledgerentry",models.Index(fields=["strategy","occurred_on"],name="tracker_led_strategy_2026_idx")),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
