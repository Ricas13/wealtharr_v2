from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

ACCOUNT_TYPES = [
    ("ISA", "Stocks & Shares ISA"), ("GIA", "General investment account"),
    ("SIPP", "SIPP / pension"), ("PAPER", "Paper tracking (virtual only)"), ("OTHER", "Other account"),
]
RISK_TYPES = [("LOW", "Low"), ("MEDIUM", "Medium"), ("HIGH", "High")]

class Account(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="investment_accounts")
    name = models.CharField(max_length=90)
    kind = models.CharField(max_length=12, choices=ACCOUNT_TYPES, default="ISA")
    currency = models.CharField(max_length=3, default="GBP", editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["owner", "name"], name="account_unique_owner_name")]

    def __str__(self):
        return self.name

class StrategyPreset(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=100)
    summary = models.TextField()
    risk = models.CharField(max_length=10, choices=RISK_TYPES)
    methodology = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["risk", "name"]

    def __str__(self):
        return self.name

class StrategyInstance(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="strategies")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="strategies")
    preset = models.ForeignKey(StrategyPreset, on_delete=models.PROTECT)
    name = models.CharField(max_length=100)
    monthly_target = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    equity_symbol = models.CharField(max_length=24, blank=True, help_text="Exact Yahoo ticker of the 3× equity trading line")
    safe_symbol = models.CharField(max_length=24, blank=True, help_text="Exact Yahoo ticker of the safe asset")
    market_data_enabled = models.BooleanField(default=False, help_text="Fetch Yahoo closes and enable advisory strategy reviews")
    contribution_alerts = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [models.UniqueConstraint(fields=["owner", "account", "name"], name="strategy_unique_account_name")]

    def clean(self):
        if self.account_id and self.owner_id and self.account.owner_id != self.owner_id:
            raise ValidationError("This account does not belong to the strategy owner.")

    def __str__(self):
        return self.name

class LedgerEntry(models.Model):
    DEPOSIT = "DEPOSIT"
    WITHDRAW = "WITHDRAW"
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"
    FEE = "FEE"
    TYPES = [
        (DEPOSIT, "Add money"), (WITHDRAW, "Withdraw money"),
        (BUY, "Buy"), (SELL, "Sell"),
        (DIVIDEND, "Dividend"), (FEE, "Other fee"),
    ]
    strategy = models.ForeignKey(StrategyInstance, on_delete=models.CASCADE, related_name="ledger")
    kind = models.CharField(max_length=12, choices=TYPES)
    occurred_on = models.DateField()
    symbol = models.CharField(max_length=24, blank=True)
    quantity = models.DecimalField(max_digits=22, decimal_places=8, default=Decimal("0"))
    unit_price = models.DecimalField(max_digits=22, decimal_places=8, default=Decimal("0"))
    amount = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0"))
    fee = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0"))
    note = models.CharField(max_length=280, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["occurred_on", "pk"]
        indexes = [models.Index(fields=["strategy", "occurred_on"], name="tracker_led_strategy_2026_idx")]

class PriceMark(models.Model):
    strategy = models.ForeignKey(StrategyInstance, on_delete=models.CASCADE, related_name="prices")
    symbol = models.CharField(max_length=24)
    price = models.DecimalField(max_digits=22, decimal_places=8)
    as_of = models.DateField()
    source = models.CharField(max_length=10, default="manual", choices=[("manual", "Manual"), ("yahoo", "Yahoo")])

    class Meta:
        constraints = [models.UniqueConstraint(fields=["strategy", "symbol"], name="unique_strategy_mark")]

class WealthSnapshot(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wealth_snapshots")
    as_of = models.DateField()
    total_value = models.DecimalField(max_digits=24, decimal_places=2)
    net_contributed = models.DecimalField(max_digits=24, decimal_places=2)
    captured_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["as_of"]
        constraints = [models.UniqueConstraint(fields=["owner", "as_of"], name="unique_owner_snapshot_day")]

class NotificationPreference(models.Model):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_preference")
    destination_email = models.EmailField(blank=True)
    email_enabled = models.BooleanField(default=False)
    discord_enabled = models.BooleanField(default=False)
    monthly_reminders = models.BooleanField(default=True)

    def __str__(self):
        return "Notifications"

class SystemIntegration(models.Model):
    # Only one row (pk=1). Secrets stored encrypted at rest.
    smtp_host = models.CharField(max_length=255, blank=True)
    smtp_port = models.PositiveIntegerField(default=587)
    smtp_username = models.CharField(max_length=255, blank=True)
    smtp_password_ciphertext = models.TextField(blank=True)
    smtp_sender = models.EmailField(blank=True)
    smtp_use_tls = models.BooleanField(default=True)
    discord_url_ciphertext = models.TextField(blank=True)

    @classmethod
    def current(cls):
        instance, _ = cls.objects.get_or_create(pk=1)
        return instance

class NotificationDelivery(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    channel = models.CharField(max_length=12)
    event_key = models.CharField(max_length=160)
    status = models.CharField(max_length=12, default="PENDING")
    created_at = models.DateTimeField(auto_now_add=True)
    error = models.CharField(max_length=200, blank=True)
    subject = models.CharField(max_length=200, blank=True)
    message = models.TextField(blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "channel", "event_key"], name="unique_notification_event")]


class MarketObservation(models.Model):
    symbol = models.CharField(max_length=24)
    as_of = models.DateField()
    price = models.DecimalField(max_digits=22, decimal_places=8)
    currency = models.CharField(max_length=3)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["symbol", "as_of"], name="unique_market_close")]
        ordering = ["as_of"]

class YahooSync(models.Model):
    symbol = models.CharField(max_length=24, unique=True)
    last_attempt = models.DateTimeField(null=True, blank=True)
    last_success = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=80, blank=True)

class StrategySignal(models.Model):
    strategy = models.ForeignKey(StrategyInstance, on_delete=models.CASCADE, related_name="signals")
    signal_key = models.CharField(max_length=80)
    kind = models.CharField(max_length=20)
    title = models.CharField(max_length=180)
    message = models.TextField()
    actions = models.JSONField(default=list)
    as_of = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["strategy", "signal_key"], name="unique_strategy_signal")]
        ordering = ["-created_at"]


class StrategyBaseline(models.Model):
    """Immutable, owner-confirmed re-anchor after an actual 9Sig reset fill.

    A reset suggestion is not a trade. The next cycle must be based on actual
    confirmed holdings, not a fictitious 60/40 rebalance.
    """
    strategy = models.ForeignKey(StrategyInstance, on_delete=models.CASCADE,
                                 related_name="baselines")
    reset_signal = models.ForeignKey(StrategySignal, on_delete=models.PROTECT)
    as_of = models.DateField()
    equity_value = models.DecimalField(max_digits=22, decimal_places=2)
    equity_quantity = models.DecimalField(max_digits=22, decimal_places=8)
    safe_value = models.DecimalField(max_digits=22, decimal_places=2)
    cash_value = models.DecimalField(max_digits=22, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["as_of", "id"]
        constraints = [
            models.UniqueConstraint(fields=["strategy", "as_of"], name="unique_strategy_baseline")
        ]
