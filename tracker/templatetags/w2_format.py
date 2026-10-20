"""Signed money and percentage formatting for the compact v2 pages."""
from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


def _number(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


@register.filter
def signed_money(value, places=2):
    """+£1,234.56 / −£1,234.56; "—" when unknown."""
    number = _number(value)
    if number is None:
        return "—"
    sign = "+" if number > 0 else "−" if number < 0 else ""
    return "%s£{:,.{p}f}".format(abs(number), p=int(places)) % sign


@register.filter
def signed_pct(value, places=1):
    """+1.2% / −1.2%; "—" when unknown."""
    number = _number(value)
    if number is None:
        return "—"
    sign = "+" if number > 0 else "−" if number < 0 else ""
    return "%s{:,.{p}f}%%".format(abs(number), p=int(places)) % sign


@register.filter
def signed_num(value, places=2):
    """+1,234.56 / −1,234.56 without a currency sign."""
    number = _number(value)
    if number is None:
        return "—"
    sign = "+" if number > 0 else "−" if number < 0 else ""
    return "%s{:,.{p}f}".format(abs(number), p=int(places)) % sign


@register.filter
def tone(value):
    """CSS class for a gain or loss."""
    number = _number(value)
    if number is None or number == 0:
        return ""
    return "w2-pos" if number > 0 else "w2-neg"
