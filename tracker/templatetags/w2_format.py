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
    """+£1,234.56 / −£1,234.56 (the page's currency symbol); "—" when unknown."""
    from tracker.money import current_symbol
    number = _number(value)
    if number is None:
        return "—"
    sign = "+" if number > 0 else "−" if number < 0 else ""
    return "%s%s{:,.{p}f}".format(abs(number), p=int(places)) % (sign, current_symbol())


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


@register.filter
def strategy_colour(instance):
    """The strategy's own colour (a fixed hex value from the registry)."""
    from tracker.strategies.registry import DEFAULT_COLOUR, colour_for
    try:
        return colour_for(instance)
    except AttributeError:
        return DEFAULT_COLOUR
