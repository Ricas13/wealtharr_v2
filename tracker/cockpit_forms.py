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

class DepositShortcutForm(forms.Form):
    amount = forms.DecimalField(min_value=Decimal('0.01'), max_digits=16, decimal_places=2)
    occurred_on = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    def clean_occurred_on(self):
        when = self.cleaned_data['occurred_on']
        if when > timezone.localdate():
            raise forms.ValidationError('A deposit cannot have a future date.')
        return when

class OneScreenSetupForm(forms.Form):
    account_kind = forms.ChoiceField(choices=[('ISA', 'Stocks & Shares ISA'), ('GIA', 'Invest / GIA')])
    starting_cash = forms.DecimalField(min_value=Decimal('0.01'), decimal_places=2, max_digits=16,
                                       initial=Decimal('1000.00'))
    start_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    position_mode = forms.ChoiceField(choices=[('FRESH', 'Starting fresh'), ('EXISTING', 'I already own investments')])
    destination = forms.ChoiceField(choices=[('NONE', 'Configure later'), ('EMAIL', 'Email'), ('DISCORD', 'Discord')])
    email = forms.EmailField(required=False)
    instrument_confirmed = forms.BooleanField(required=True,
        label='I checked availability and suitability of the mapped securities in my broker account.')

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('start_date') and cleaned['start_date'] > timezone.localdate():
            self.add_error('start_date', 'Start date cannot be in the future.')
        if cleaned.get('destination') == 'EMAIL' and not cleaned.get('email'):
            self.add_error('email', 'Enter an email destination.')
        return cleaned

class ExecutionDateForm(forms.Form):
    executed_on = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    def clean_executed_on(self):
        when = self.cleaned_data['executed_on']
        if when > timezone.localdate():
            raise forms.ValidationError('Execution date cannot be in the future.')
        return when
