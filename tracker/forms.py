import re
from decimal import Decimal
from django import forms
from django.utils import timezone
from .models import Account, StrategyInstance, StrategyPreset, LedgerEntry, NotificationPreference, SystemIntegration
from .services import encrypt_secret

ZERO = Decimal("0")

def style(fields):
    for field in fields.values():
        field.widget.attrs.setdefault("class", "field")
        if isinstance(field.widget, forms.CheckboxInput):
            field.widget.attrs["class"] = "check"

class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = ["name", "kind"]
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style(self.fields)

class StrategyForm(forms.ModelForm):
    class Meta:
        model = StrategyInstance
        fields = ["name", "account", "preset", "equity_symbol", "safe_symbol", "market_data_enabled", "monthly_target", "contribution_alerts"]
        labels = {"name": "Tab name", "preset": "Strategy", "monthly_target": "Monthly contribution target (£)"}
        help_texts = {"name": "e.g. 9Sig — ISA or 9Sig — Trading account"}
    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(owner=owner)
        self.fields["preset"].queryset = StrategyPreset.objects.filter(active=True)
        self.fields["monthly_target"].min_value = ZERO
        if not self.is_bound:
            self.fields["monthly_target"].initial = Decimal("500")
        style(self.fields)
    def clean(self):
        data = super().clean()
        enabled = data.get("market_data_enabled")
        equity = (data.get("equity_symbol") or "").strip().upper()
        safe = (data.get("safe_symbol") or "").strip().upper()
        data["equity_symbol"], data["safe_symbol"] = equity, safe
        if enabled:
            if not data.get("preset") or data["preset"].slug != "nasdaq-quarterly-target":
                raise forms.ValidationError("Automated 9Sig reviews are only available for the Nasdaq 3× quarterly model.")
            pattern = r"[A-Z0-9][A-Z0-9.^=_-]{0,23}"
            if not re.fullmatch(pattern, equity) or not re.fullmatch(pattern, safe) or equity == safe:
                raise forms.ValidationError("Enter two distinct exact Yahoo tickers for the equity and safe holdings.")
        return data

class AutomationForm(forms.ModelForm):
    class Meta:
        model = StrategyInstance
        fields = ["equity_symbol", "safe_symbol", "market_data_enabled"]
        labels = {"equity_symbol": "GBP Yahoo equity ticker", "safe_symbol": "GBP Yahoo safe-asset ticker",
                  "market_data_enabled": "Enable automatic Yahoo closes and advisory 9Sig reviews"}
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .instrument_mapping import for_strategy
        self.instrument_profile = for_strategy(self.instance)
        if self.instrument_profile:
            # Instrument selection is owned by the account mapping, not by
            # a free-text field or an altered POST payload.
            del self.fields["equity_symbol"]
            del self.fields["safe_symbol"]
        style(self.fields)
    def clean(self):
        data = super().clean()
        if self.instrument_profile:
            eq = self.instrument_profile.equity.yahoo_ticker
            safe = self.instrument_profile.safe.yahoo_ticker
        else:
            eq = (data.get("equity_symbol") or "").strip().upper()
            safe = (data.get("safe_symbol") or "").strip().upper()
            data.update(equity_symbol=eq, safe_symbol=safe)
        if data.get("market_data_enabled") and (
            self.instance.preset.slug != "nasdaq-quarterly-target" or
            not re.fullmatch(r"[A-Z0-9][A-Z0-9.^=_-]{0,23}", eq) or
            not re.fullmatch(r"[A-Z0-9][A-Z0-9.^=_-]{0,23}", safe) or eq == safe
        ):
            raise forms.ValidationError("Enter two distinct valid Yahoo tickers for Nasdaq 3× monitoring.")
        return data

class LedgerForm(forms.ModelForm):
    class Meta:
        model = LedgerEntry
        fields = ["kind", "occurred_on", "amount", "symbol", "quantity", "unit_price", "fee", "note"]
        widgets = {"occurred_on": forms.DateInput(attrs={"type": "date"})}
        labels = {
            "amount": "Cash amount (£) — deposits, withdrawals, dividends or fees",
            "quantity": "Units — buys/sells only",
            "unit_price": "Execution price per unit (£)",
            "fee": "Trade fee (£)",
            "symbol": "Ticker / instrument",
        }
    def __init__(self, *args, strategy=None, **kwargs):
        super().__init__(*args, **kwargs)
        from .instrument_mapping import for_strategy
        self.instrument_profile = for_strategy(strategy) if strategy else None
        self.fields["occurred_on"].initial = timezone.localdate()
        for name in ("amount", "quantity", "unit_price", "fee", "symbol"):
            self.fields[name].required = False
        if self.instrument_profile:
            self.fields["symbol"].widget = forms.Select(choices=[
                ("", "Select the instrument for a buy/sell"),
                (self.instrument_profile.equity.yahoo_ticker,
                 "LQQ3 — 3x Nasdaq-100 equity"),
                (self.instrument_profile.safe.yahoo_ticker,
                 "ERNS — defensive bond sleeve"),
            ])
            self.fields["symbol"].help_text = (
                "Wealtharr records the correct Yahoo trading-line symbol for you.")
        style(self.fields)
    def clean(self):
        data = super().clean()
        if self.errors:
            return data
        kind = data.get("kind")
        amount = data.get("amount") or ZERO
        qty = data.get("quantity") or ZERO
        price = data.get("unit_price") or ZERO
        fee = data.get("fee") or ZERO
        symbol = (data.get("symbol") or "").strip().upper()
        when = data.get("occurred_on")
        if when and when > timezone.localdate():
            raise forms.ValidationError("A transaction cannot have a future execution date.")
        if min(amount, qty, price, fee) < ZERO:
            raise forms.ValidationError("Amounts, quantities, prices and fees cannot be negative.")
        if kind in (LedgerEntry.BUY, LedgerEntry.SELL):
            if not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,23}", symbol):
                raise forms.ValidationError("Enter a valid ticker (for example LQQ3 or VWRP.L).")
            if self.instrument_profile and symbol not in (
                self.instrument_profile.equity.yahoo_ticker,
                self.instrument_profile.safe.yahoo_ticker,
            ):
                raise forms.ValidationError(
                    "Select one of the two 9Sig instruments Wealtharr mapped to your account.")
            if qty <= ZERO or price <= ZERO:
                raise forms.ValidationError("Buys and sells need positive units and a positive execution price.")
            if amount != ZERO:
                raise forms.ValidationError("For trades, leave cash amount empty. Proceeds are calculated from units × price.")
        else:
            if amount <= ZERO:
                raise forms.ValidationError("Enter a positive cash amount.")
            if symbol or qty or price or fee:
                raise forms.ValidationError("For cash movements, use cash amount only; clear the ticker, units, price and trade fee.")
        data.update(amount=amount, quantity=qty, unit_price=price, fee=fee, symbol=symbol)
        return data

class PriceForm(forms.Form):
    symbol = forms.CharField(max_length=24, label="Ticker to value")
    price = forms.DecimalField(min_value=Decimal("0.00000001"), max_digits=22, decimal_places=8, label="Latest price per unit (£)")
    as_of = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="Price date")
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["as_of"].initial = timezone.localdate()
        style(self.fields)
    def clean_symbol(self):
        symbol = self.cleaned_data["symbol"].strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,23}", symbol):
            raise forms.ValidationError("Invalid ticker.")
        return symbol
    def clean_as_of(self):
        date = self.cleaned_data["as_of"]
        if date > timezone.localdate():
            raise forms.ValidationError("Future prices are not permitted.")
        return date

class NotificationForm(forms.ModelForm):
    """Where alerts go. Which alerts go where is chosen per strategy."""
    class Meta:
        model = NotificationPreference
        fields = ["destination_email", "email_enabled", "discord_enabled"]
        labels = {
            "destination_email": "Send emails to",
            "email_enabled": "Enable email notifications",
            "discord_enabled": "Enable Discord notifications",
        }
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style(self.fields)

class IntegrationForm(forms.ModelForm):
    smtp_password = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False), label="SMTP password (leave blank to keep existing)")
    discord_client_secret = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False), label="Discord client secret (leave blank to keep existing)")
    discord_bot_token = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False), label="Discord bot token (leave blank to keep existing)")
    class Meta:
        model = SystemIntegration
        fields = ["smtp_host", "smtp_port", "smtp_username", "smtp_sender", "smtp_use_tls",
                  "discord_client_id", "discord_guild_id"]
        labels = {
            "smtp_host": "SMTP server", "smtp_port": "SMTP port",
            "smtp_username": "SMTP username", "smtp_sender": "From email",
            "smtp_use_tls": "Use STARTTLS",
            "discord_client_id": "Discord application ID",
            "discord_guild_id": "Discord server ID (where the bot lives)",
        }
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style(self.fields)
    def _snowflake(self, name):
        value = (self.cleaned_data.get(name) or "").strip()
        if value and not value.isdigit():
            raise forms.ValidationError("Discord IDs are numbers only (copy it with Developer Mode on).")
        return value
    def clean_discord_client_id(self):
        return self._snowflake("discord_client_id")
    def clean_discord_guild_id(self):
        return self._snowflake("discord_guild_id")
    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.cleaned_data.get("smtp_password"):
            obj.smtp_password_ciphertext = encrypt_secret(self.cleaned_data["smtp_password"])
        for field in ("discord_client_secret", "discord_bot_token"):
            if self.cleaned_data.get(field):
                setattr(obj, field + "_ciphertext", encrypt_secret(self.cleaned_data[field].strip()))
        if commit:
            obj.save()
        return obj


class RealNineSigSetupForm(forms.Form):
    """Select a strategy and wrapper; the server resolves actual instruments."""
    strategy_slug = forms.ChoiceField(
        choices=(), label="Strategy")
    country = forms.ChoiceField(
        choices=[("GB", "United Kingdom")], initial="GB", label="Country")
    broker = forms.ChoiceField(
        choices=[("TRADING212", "Trading 212"), ("OTHER", "Other broker (not yet supported)")],
        initial="TRADING212", label="Broker")
    account_kind = forms.ChoiceField(
        choices=[("ISA", "Stocks & Shares ISA"), ("GIA", "General investment account")],
        initial="ISA", label="Account type")
    account_name = forms.CharField(
        max_length=90, initial="Trading 212 ISA",
        label="Investment account name")
    strategy_name = forms.CharField(
        max_length=100, initial="9Sig — Trading 212",
        label="Strategy tab name")
    start_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Date you added the initial cash")
    starting_cash = forms.DecimalField(
        max_digits=16, decimal_places=2, min_value=Decimal("0.01"),
        initial=Decimal("1000.00"), label="Actual starting cash deposited (£)")
    monthly_target = forms.DecimalField(
        max_digits=16, decimal_places=2, min_value=ZERO,
        initial=Decimal("500.00"), label="Planned monthly contribution (£)")
    confirmed_broker_availability = forms.BooleanField(
        required=True,
        label="I checked the listed ISINs in my own Trading 212 account and understand that availability and complex-product access are not guaranteed.")

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner
        supported = StrategyPreset.objects.filter(
            active=True, slug="nasdaq-quarterly-target")
        self.fields["strategy_slug"].choices = [
            (preset.slug, preset.name) for preset in supported
        ]
        if not self.is_bound:
            self.fields["strategy_slug"].initial = "nasdaq-quarterly-target"
        self.fields["start_date"].initial = timezone.localdate()
        self.instrument_profile = None
        style(self.fields)

    def clean(self):
        from .instrument_mapping import (
            resolve_instruments, InstrumentMappingUnavailable)
        values = super().clean()
        day = values.get("start_date")
        if day and day > timezone.localdate():
            self.add_error("start_date", "You cannot record a future cash deposit.")
        for key in ("strategy_slug", "country", "broker", "account_kind"):
            if key not in values:
                return values
        try:
            self.instrument_profile = resolve_instruments(
                values["strategy_slug"], values["country"],
                values["account_kind"], values["broker"])
        except InstrumentMappingUnavailable as exc:
            raise forms.ValidationError(str(exc))
        if values.get("account_name"):
            existing = Account.objects.filter(
                owner=self.owner, name=values["account_name"]).first()
            if existing and (existing.kind != values["account_kind"] or
                             existing.country != values["country"] or
                             existing.broker not in ("UNKNOWN", values["broker"])):
                self.add_error(
                    "account_name",
                    "That name already belongs to a different account type, country or broker.")
        return values
