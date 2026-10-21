"""Calendar helpers shared by every strategy. No strategy rules live here."""
from calendar import monthrange
from datetime import date


def quarter_end_before(day):
    """Last completed calendar quarter end before ``day``."""
    if day.month <= 3:
        return date(day.year - 1, 12, 31)
    if day.month <= 6:
        return date(day.year, 3, 31)
    if day.month <= 9:
        return date(day.year, 6, 30)
    return date(day.year, 9, 30)


def quarter_end_on_or_after(day):
    """The end of the calendar quarter containing ``day``."""
    month = ((day.month - 1) // 3 + 1) * 3
    return date(day.year, month, monthrange(day.year, month)[1])


def year_end_before(day):
    """Last completed calendar year end before ``day``."""
    return date(day.year - 1, 12, 31)


def year_end_on_or_after(day):
    return date(day.year, 12, 31)


def month_end_before(day):
    """Last completed calendar month end before ``day``."""
    first = date(day.year, day.month, 1)
    previous = date(first.year - 1, 12, 1) if first.month == 1 else date(first.year, first.month - 1, 1)
    return date(previous.year, previous.month, monthrange(previous.year, previous.month)[1])


def month_end_on_or_after(day):
    return date(day.year, day.month, monthrange(day.year, day.month)[1])


def months_before(day, months):
    """The month end ``months`` calendar months before month end ``day``."""
    index = day.year * 12 + day.month - 1 - months
    year, month = divmod(index, 12)
    return date(year, month + 1, monthrange(year, month + 1)[1])
