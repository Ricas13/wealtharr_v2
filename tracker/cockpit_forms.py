from decimal import Decimal
from django import forms
from django.utils import timezone

class FillForm(forms.Form):
    quantity = forms.DecimalField(min_value=Decimal('0.00000001'), max_digits=22, decimal_places=8,
                                  widget=forms.NumberInput(attrs={'step': 'any', 'class': 'field'}))
    unit_price = forms.DecimalField(min_value=Decimal('0.00000001'), max_digits=22, decimal_places=8,
                                    widget=forms.NumberInput(attrs={'step': 'any', 'class': 'field'}))
    fee = forms.DecimalField(min_value=Decimal('0'), max_digits=20, decimal_places=2,
                             widget=forms.NumberInput(attrs={'step': '0.01', 'class': 'field'}))


class SingleSignalFillForm(forms.Form):
    """One historical execution; selection is only from server-pinned advice."""
    action_index = forms.IntegerField(min_value=0, max_value=3,
                                      widget=forms.HiddenInput())
    executed_on = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": "field"}))
    quantity = forms.DecimalField(
        min_value=Decimal("0.00000001"), max_digits=22, decimal_places=8,
        widget=forms.NumberInput(attrs={"step": "any", "class": "field"}))
    unit_price = forms.DecimalField(
        min_value=Decimal("0.00000001"), max_digits=22, decimal_places=8,
        widget=forms.NumberInput(attrs={"step": "any", "class": "field"}))
    fee = forms.DecimalField(
        min_value=Decimal("0"), max_digits=20, decimal_places=2,
        initial=Decimal("0"),
        widget=forms.NumberInput(attrs={"step": "0.01", "class": "field"}))
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput())

    def clean_executed_on(self):
        executed_on = self.cleaned_data["executed_on"]
        if executed_on > timezone.localdate():
            raise forms.ValidationError("The broker execution cannot be in the future.")
        return executed_on

class DepositShortcutForm(forms.Form):
    amount = forms.DecimalField(min_value=Decimal('0.01'), max_digits=16, decimal_places=2)
    occurred_on = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    # Optional estimate only. Never save this as a deposit or assert it was
    # broker-verified; it is money deposited into other ISAs in this tax year.
    other_isa_subscriptions = forms.DecimalField(
        required=False, min_value=Decimal("0"), max_digits=16,
        decimal_places=2, label="Other ISA subscriptions this tax year (£)")
    def clean_occurred_on(self):
        when = self.cleaned_data['occurred_on']
        if when > timezone.localdate():
            raise forms.ValidationError('A deposit cannot have a future date.')
        return when

class OneScreenSetupForm(forms.Form):
    """The only supported new enrolment: GB / Trading 212 / Stocks ISA.

    Optional recorded opening cash is NOT the £1,000 illustrative plan. A
    blank field creates no ledger event. Existing portfolios must import
    actual historical activity; never fabricate starting shares or cost.
    """
    position_mode = forms.ChoiceField(
        choices=[("FRESH", "I am starting from cash"),
                 ("EXISTING", "I already have investments in Trading 212")],
        initial="FRESH", label="How would you like to start?")
    starting_cash = forms.DecimalField(
        required=False, min_value=Decimal("0.01"),
        max_digits=16, decimal_places=2,
        label="Cash already deposited (£) — optional",
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0.01",
                                        "inputmode": "decimal", "placeholder": "Leave blank if not deposited"}))
    start_date = forms.DateField(required=False, label="Actual deposit date",
                                 widget=forms.DateInput(attrs={"type": "date"}))
    monthly_target = forms.DecimalField(
        required=True, min_value=Decimal("0"), max_digits=16,
        decimal_places=2, initial=Decimal("500.00"),
        label="Planned monthly contribution (£)",
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0",
                                        "inputmode": "decimal"}))
    destination = forms.ChoiceField(
        choices=[("NONE", "I'll set up alerts later"), ("EMAIL", "Email"),
                 ("DISCORD", "Discord (configure in Settings)")],
        initial="NONE", label="How should I receive alerts?")
    email = forms.EmailField(required=False, label="Alert email (if selected)",
                             widget=forms.EmailInput(attrs={"autocomplete": "email"}))

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get("position_mode")
        cash = cleaned.get("starting_cash")
        when = cleaned.get("start_date")
        if when and when > timezone.localdate():
            self.add_error("start_date", "A recorded deposit cannot be in the future.")
        if mode == "EXISTING" and cash is not None:
            self.add_error("starting_cash",
                           "Existing holdings must come from actual complete account history; do not add a second opening deposit.")
        if mode == "EXISTING" and when is not None:
            self.add_error("start_date",
                           "Do not supply an invented opening date. Import the original history instead.")
        if mode == "FRESH" and cash is not None and not when:
            self.add_error("start_date", "Enter the date this money actually arrived in Trading 212.")
        if mode == "FRESH" and cash is None and when is not None:
            self.add_error("starting_cash",
                           "Enter an actual deposited amount or leave both opening-cash fields blank.")
        if cleaned.get("destination") == "EMAIL" and not cleaned.get("email"):
            self.add_error("email", "Enter an alert email address or configure alerts later.")
        return cleaned

class ExecutionDateForm(forms.Form):
    executed_on = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    def clean_executed_on(self):
        when = self.cleaned_data['executed_on']
        if when > timezone.localdate():
            raise forms.ValidationError('Execution date cannot be in the future.')
        return when
