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
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["occurred_on"].initial = timezone.localdate()
        for name in ("amount", "quantity", "unit_price", "fee", "symbol"):
            self.fields[name].required = False
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
    class Meta:
        model = NotificationPreference
        fields = ["destination_email", "email_enabled", "discord_enabled", "monthly_reminders"]
        labels = {
            "destination_email": "Send emails to",
            "email_enabled": "Enable email notifications",
            "discord_enabled": "Enable Discord notifications",
            "monthly_reminders": "Remind me of planned monthly contributions",
        }
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style(self.fields)

class IntegrationForm(forms.ModelForm):
    smtp_password = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False), label="SMTP password (leave blank to keep existing)")
    discord_webhook = forms.URLField(required=False, widget=forms.PasswordInput(render_value=False), label="Discord webhook URL (leave blank to keep existing)")
    class Meta:
        model = SystemIntegration
        fields = ["smtp_host", "smtp_port", "smtp_username", "smtp_sender", "smtp_use_tls"]
        labels = {
            "smtp_host": "SMTP server", "smtp_port": "SMTP port",
            "smtp_username": "SMTP username", "smtp_sender": "From email",
            "smtp_use_tls": "Use STARTTLS",
        }
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style(self.fields)
    def clean_discord_webhook(self):
        url = self.cleaned_data.get("discord_webhook", "")
        if url and not (url.startswith("https://discord.com/api/webhooks/") or url.startswith("https://discordapp.com/api/webhooks/")):
            raise forms.ValidationError("Only official HTTPS Discord webhook URLs are accepted.")
        return url
    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.cleaned_data.get("smtp_password"):
            obj.smtp_password_ciphertext = encrypt_secret(self.cleaned_data["smtp_password"])
        if self.cleaned_data.get("discord_webhook"):
            obj.discord_url_ciphertext = encrypt_secret(self.cleaned_data["discord_webhook"])
        if commit:
            obj.save()
        return obj
