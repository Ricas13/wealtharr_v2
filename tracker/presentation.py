"""Presentation-only paths from verified strategy valuation snapshots.

Never interpolate portfolio values, reconstruct missing price dates or
misrepresent a model backtest as the owner's brokerage performance.
"""
from decimal import Decimal


def sparkline_path(points, width=112, height=32):
    """Return an SVG path through dated (date, GBP value) observations."""
    if len(points) < 2:
        return ""
    ordered = sorted(points, key=lambda row: row[0])
    dates = [row[0] for row in ordered]
    values = [Decimal(row[1]) for row in ordered]
    lo, hi = min(values), max(values)
    span = hi - lo
    days = (dates[-1] - dates[0]).days
    coords = []
    for index, (day, value) in enumerate(zip(dates, values)):
        x = ((day - dates[0]).days / days * width) if days else index / (len(ordered) - 1) * width
        y = (height / 2 if span == 0 else float((hi - value) / span * height))
        coords.append(("%s%.1f %.1f" % ("M" if index == 0 else "L", x, y)))
    return " ".join(coords)
