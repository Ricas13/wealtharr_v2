from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

ACCOUNT_TYPES = [
    ("ISA", "Stocks & Shares ISA"), ("GIA", "General investment account"),
    ("SIPP", "SIPP / pension"), ("OTHER", "Other account"),
]
RISK_TYPES = [("LOW", "Low"), ("MEDIUM", "Medium"), ("HIGH", "High")]

class Account(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="investment_accounts")
    name = models.CharField(max_length=90)
    kind = models.CharField(max_length=12, choices=ACCOUNT_TYPES, default="ISA")
    currency = models.CharField(max_length=3, default="GBP", editable=False)
    country = models.CharField(max_length=2, default="GB")
    broker = models.CharField(max_length=32, default="UNKNOWN")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["owner", "name"], name="account_unique_owner_name")]

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values(
                "owner_id", "kind", "broker", "country", "currency"
            ).first()
            if original and any(original[key] != getattr(self, key)
                                for key in original):
                if self.strategies.exclude(strategy_key="").exists():
                    raise ValidationError(
                        "Platform, account type and owner are immutable for enrolled strategies."
                    )
        return super().save(*args, **kwargs)

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
    # V2 enrolment identity snapshots. Old instances stay unconverted until an
    # explicit owner-approved migration, never silently inherit a new version.
    strategy_key = models.CharField(max_length=100, blank=True, default="", editable=False)
    platform_code = models.CharField(max_length=32, blank=True, default="", editable=False)
    account_type_code = models.CharField(max_length=12, blank=True, default="", editable=False)
    started_on = models.DateField(blank=True, null=True, editable=False)
    owner_checked_on = models.DateField(blank=True, null=True, editable=False)
    tracking_only = models.BooleanField(default=False, editable=False)
    alert_routes = models.JSONField(default=dict, blank=True)
    onboarding_mode = models.CharField(max_length=12, blank=True, default="", editable=False)
    # Source-controlled, owner-enrolment instrument manifest; empty for legacy
    # instances until an explicit, owner-approved migration is implemented.
    strategy_version = models.CharField(max_length=48, blank=True, default="", editable=False)
    mapping_version = models.CharField(max_length=40, blank=True, default="", editable=False)
    resolved_instruments = models.JSONField(blank=True, default=dict, editable=False)
    market_data_enabled = models.BooleanField(default=False, help_text="Fetch Yahoo closes and enable advisory strategy reviews")
    contribution_alerts = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [models.UniqueConstraint(fields=["owner", "account", "name"], name="strategy_unique_account_name")]

    def clean(self):
        if self.account_id and self.owner_id and self.account.owner_id != self.owner_id:
            raise ValidationError("This account does not belong to the strategy owner.")

    V2_IDENTITY_FIELDS = (
        "owner_id", "account_id", "preset_id", "strategy_key",
        "platform_code", "account_type_code", "started_on",
        "strategy_version", "mapping_version", "resolved_instruments",
        "onboarding_mode", "owner_checked_on",
    )

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                *self.V2_IDENTITY_FIELDS
            ).first()
            if previous and previous["strategy_key"]:
                if any(previous[field] != getattr(self, field)
                       for field in self.V2_IDENTITY_FIELDS):
                    raise ValidationError(
                        "An enrolled strategy's identity and mapping are immutable. "
                        "Create a separate instance for another account."
                    )
        if self.strategy_key:
            self.clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class InstanceOpeningSnapshot(models.Model):
    """Owner-reported initial positions; NOT verified trades or prices.

    Phase 3 must reconcile before the snapshot enters ledger-based performance.
    This immutable record deliberately does not create fictional BUY entries.
    """
    strategy = models.OneToOneField(
        StrategyInstance, on_delete=models.PROTECT, related_name="opening_snapshot"
    )
    reported_on = models.DateField()
    cash_gbp = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0"))
    positions = models.JSONField(default=dict)
    recorded_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Opening snapshot is immutable. Reconciliation is audited separately.")
        if self.cash_gbp < 0:
            raise ValidationError("Opening cash cannot be negative.")
        return super().save(*args, **kwargs)


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



class OpeningReconciliation(models.Model):
    """Append-only evidence that imported events reproduce owner-reported holdings."""
    opening = models.OneToOneField(
        InstanceOpeningSnapshot, on_delete=models.PROTECT,
        related_name="reconciliation",
    )
    file_sha256 = models.CharField(max_length=64)
    accepted_rows = models.PositiveIntegerField()
    reconciled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT
    )
    reconciled_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Reconciliation evidence is immutable.")
        if self.opening_id and self.reconciled_by_id and (
                self.opening.strategy.owner_id != self.reconciled_by_id):
            raise ValidationError("Another owner's opening snapshot cannot be reconciled.")
        return super().save(*args, **kwargs)



class LedgerRevision(models.Model):
    """Append-only audited overlay; original LedgerEntry rows never change.

    The most recent revision provides an effective entry or soft deletion.
    Changes to source-linked broker fills and confirmed reviews are forbidden.
    """
    entry = models.ForeignKey(
        LedgerEntry, on_delete=models.PROTECT, related_name="revisions"
    )
    strategy = models.ForeignKey(
        StrategyInstance, on_delete=models.PROTECT, related_name="ledger_revisions"
    )
    version = models.PositiveIntegerField()
    operation = models.CharField(
        max_length=10,
        choices=[
            ("ADD", "Added"), ("EDIT", "Edited"),
            ("DELETE", "Soft deleted"), ("RESTORE", "Restored"),
        ],
    )
    before = models.JSONField(blank=True, null=True)
    after = models.JSONField(blank=True, null=True)
    impact = models.JSONField(default=dict)
    reason = models.CharField(max_length=280, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["entry_id", "version"]
        constraints = [
            models.UniqueConstraint(
                fields=["entry", "version"], name="unique_ledger_revision"
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Ledger revisions are immutable.")
        if (self.entry_id and self.strategy_id and
                self.entry.strategy_id != self.strategy_id):
            raise ValidationError("Ledger revision strategy mismatch.")
        if self.actor_id and self.strategy_id and (
                self.strategy.owner_id != self.actor_id):
            raise ValidationError("Ledger revision owner mismatch.")
        return super().save(*args, **kwargs)



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
    # Owner-selected delay before a weekly outstanding-review reminder.
    followup_delay_days = models.PositiveSmallIntegerField(default=7)
    # Category-by-channel routing: master destination toggles still apply.
    quarterly_email = models.BooleanField(default=True)
    quarterly_discord = models.BooleanField(default=True)
    urgent_email = models.BooleanField(default=True)
    urgent_discord = models.BooleanField(default=True)
    data_email = models.BooleanField(default=True)
    data_discord = models.BooleanField(default=True)
    followup_email = models.BooleanField(default=True)
    followup_discord = models.BooleanField(default=True)
    contribution_email = models.BooleanField(default=True)
    contribution_discord = models.BooleanField(default=True)

    def __str__(self):
        return "Notifications"

class OwnerTOTP(models.Model):
    """Optional per-account authenticator factor; encrypted secret, no recovery bypass."""
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                 related_name="totp_settings")
    secret_ciphertext = models.TextField()
    enabled_at = models.DateTimeField()
    last_verified_step = models.BigIntegerField(default=-1)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return "Authenticator enabled"


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
    # Persist immutable original strategy provenance for opt-out-safe retries.
    strategy = models.ForeignKey(
        StrategyInstance, on_delete=models.PROTECT, null=True, blank=True,
        related_name="notification_receipts",
    )
    channel = models.CharField(max_length=12)
    # Fixed-category provenance lets due retries honor changed owner preferences.
    # Empty for legacy one-off notices which must not be guessed as a category.
    category = models.CharField(max_length=16, blank=True, default="")
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
    # Captured only from a source response, never inferred from old GBP values.
    source = models.CharField(max_length=16, blank=True, default="")
    raw_quote_unit = models.CharField(max_length=8, blank=True, default="")
    raw_close = models.DecimalField(
        max_digits=22, decimal_places=8, blank=True, null=True
    )
    raw_unit_verified = models.BooleanField(default=False)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["symbol", "as_of"], name="unique_market_close")]
        ordering = ["as_of"]

class NotificationSchedulerRun(models.Model):
    """One diagnostic record per scheduled notice run; never stores credentials."""
    name = models.CharField(max_length=40, default="owner_notices")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=[
        ("RUNNING", "Running"), ("SUCCEEDED", "Succeeded"), ("FAILED", "Failed"),
    ], default="RUNNING")
    new_deliveries = models.PositiveIntegerField(default=0)
    attempted_retries = models.PositiveIntegerField(default=0)
    successful_retries = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=40, blank=True, default="")

    class Meta:
        ordering = ["-started_at", "-pk"]


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
    # Explicit rule output; never detect a financial reset from free-text.
    reset_reason = models.CharField(max_length=240, blank=True, default="")
    # Immutable ruleset provenance on every newly generated review.
    strategy_version = models.CharField(max_length=48, blank=True, default="")
    as_of = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    review_outcome = models.CharField(
        max_length=16, blank=True, default="",
        choices=[
            ("CONFIRMED", "Confirmed after review"),
            ("FALSE_POSITIVE", "False signal"),
            ("NOT_APPLICABLE", "Not applicable"),
        ],
    )
    review_note = models.TextField(blank=True, default="")
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
    strategy_version = models.CharField(max_length=48, blank=True, default="", editable=False)
    mapping_version = models.CharField(max_length=40, blank=True, default="", editable=False)
    quote_evidence = models.JSONField(blank=True, default=dict, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["as_of", "id"]
        constraints = [
            models.UniqueConstraint(fields=["strategy", "as_of"], name="unique_strategy_baseline")
        ]


class StrategyValueSnapshot(models.Model):
    """Historic values are isolated by strategy and account."""
    strategy = models.ForeignKey(StrategyInstance, on_delete=models.CASCADE,
                                 related_name="value_snapshots")
    as_of = models.DateField()
    total_value = models.DecimalField(max_digits=24, decimal_places=2)
    net_contributed = models.DecimalField(max_digits=24, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["as_of"]
        constraints = [
            models.UniqueConstraint(fields=["strategy", "as_of"],
                                    name="unique_strategy_value_date")
        ]



class RebalanceExecution(models.Model):
    """A confirmed group of manual broker fills; one execution per signal."""
    signal = models.OneToOneField(StrategySignal, on_delete=models.PROTECT, related_name="execution")
    strategy = models.ForeignKey(StrategyInstance, on_delete=models.PROTECT)
    executed_on = models.DateField()
    completed_at = models.DateTimeField(auto_now_add=True)
    baseline = models.ForeignKey(StrategyBaseline, null=True, blank=True, on_delete=models.PROTECT)


class SignalFill(models.Model):
    """One actual owner-recorded execution linked to one immutable proposal.

    Multiple partial fills of a proposal are possible. The ledger remains the
    authoritative accounting record; this link captures which suggested action
    the owner was reporting and prevents duplicate request retries.
    """
    strategy = models.ForeignKey(StrategyInstance, on_delete=models.PROTECT,
                                 related_name="signal_fills")
    signal = models.ForeignKey(StrategySignal, on_delete=models.PROTECT,
                               related_name="confirmed_fills")
    ledger_entry = models.OneToOneField(LedgerEntry, on_delete=models.PROTECT,
                                         related_name="confirmed_signal_fill")
    action_index = models.PositiveSmallIntegerField()
    idempotency_key = models.CharField(max_length=36)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["ledger_entry__occurred_on", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["signal", "idempotency_key"],
                                    name="unique_signal_fill_request"),
        ]


class BrokerImportRow(models.Model):
    """Idempotent identifier for an actual imported broker event."""
    strategy = models.ForeignKey(StrategyInstance, on_delete=models.PROTECT)
    fingerprint = models.CharField(max_length=64)
    ledger_entry = models.OneToOneField(LedgerEntry, on_delete=models.PROTECT)
    source = models.CharField(max_length=24, default="trading212_csv")
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["strategy", "fingerprint"], name="unique_t212_event")]
