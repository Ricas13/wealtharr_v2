"""Money-weighted performance. No float arithmetic in financial calculations.

TWR needs valuation boundaries at EVERY cash flow. Snapshot series that lacks
those boundaries is deliberately not treated as time-weighted performance.
"""
from datetime import date
from decimal import Decimal, localcontext

D = Decimal
ZERO = D('0')
ONE = D('1')
HUNDRED = D('100')


def cash_gain(value, deposits, withdrawals=ZERO):
    if value is None:
        return None
    return value - deposits + withdrawals


def simple_return(value, deposits, withdrawals=ZERO):
    """Only meaningful for an initial deposit and NO subsequent external flows.
    Callers must enforce this condition; zero invested is undefined.
    """
    if value is None or deposits <= ZERO or withdrawals != ZERO:
        return None
    return value / deposits - ONE


def xirr(flows, end_date: date, ending_value: D):
    """Annualized money-weighted rate; inflows to portfolio are negative.

    flows: [(date, investor_cash_flow)] with deposits NEGATIVE and withdrawals
    POSITIVE. Add positive ending liquidation value internally. Multiple sign
    changes are declined because multiple IRRs can exist.
    """
    if ending_value is None or ending_value < ZERO or not flows:
        return None
    grouped = {}
    for day, amount in flows:
        if day > end_date or not D(amount).is_finite():
            return None
        grouped[day] = grouped.get(day, ZERO) + D(amount)
    grouped[end_date] = grouped.get(end_date, ZERO) + ending_value
    ordered = [(k, v) for k, v in sorted(grouped.items()) if v]
    if len(ordered) < 2 or ordered[0][0] == ordered[-1][0]:
        return None
    signs = [v > ZERO for _, v in ordered]
    if sum(a != b for a, b in zip(signs, signs[1:])) != 1:
        return None
    origin = ordered[0][0]
    with localcontext() as ctx:
        ctx.prec = 38
        times = [(D((day - origin).days) / D('365.25'), amt) for day, amt in ordered]
        def npv(rate):
            base = ONE + rate
            return sum((amt / (base ** years) for years, amt in times), ZERO)
        lo, hi = D('-0.999999'), D('1')
        try:
            a, b = npv(lo), npv(hi)
            for _ in range(32):
                if a * b <= ZERO:
                    break
                hi = hi * 2 + ONE
                b = npv(hi)
            if a * b > ZERO:
                return None
            for _ in range(180):
                mid = (lo + hi) / 2
                f = npv(mid)
                if abs(f) <= D('1e-22'):
                    return +mid
                if f * a > ZERO:
                    lo, a = mid, f
                else:
                    hi = mid
            return +(lo + hi) / 2
        except (ArithmeticError, OverflowError, ValueError):
            return None


def cumulative_from_annualized(rate, start_date, end_date):
    """Compounded money-weighted period-equivalent, NOT gain / deposits."""
    if rate is None or start_date >= end_date or rate <= -ONE:
        return None
    with localcontext() as ctx:
        ctx.prec = 38
        return (ONE + rate) ** (D((end_date - start_date).days) / D('365.25')) - ONE


def time_weighted_return(periods):
    """Exact chaining IF each period is bracketed by actual pre/post-flow NAVs.

    periods: [(start_value_after_prior_flows, end_value_before_next_flows)].
    Never feed daily totals with intervening contributions without boundaries.
    """
    if not periods:
        return None
    factor = ONE
    for start, end in periods:
        if start <= ZERO or end < ZERO:
            return None
        factor *= D(end) / D(start)
    return factor - ONE


def annualized_return(period_return, first_date, last_date):
    if period_return is None or last_date <= first_date or period_return <= -ONE:
        return None
    with localcontext() as ctx:
        ctx.prec = 38
        return (ONE + period_return) ** (D('365.25') / D((last_date - first_date).days)) - ONE


def max_drawdown(unit_navs):
    """On *unitised* NAVs, never raw value when contributions occur."""
    peak = None
    worst = ZERO
    if not unit_navs:
        return None
    for value in unit_navs:
        nav = D(value)
        if nav <= ZERO:
            return None
        peak = nav if peak is None else max(peak, nav)
        worst = max(worst, ONE - nav / peak)
    return worst


def snapshot_twr_ready(snapshots, ledger):
    """Conservatively certify TWR only when no external flow is between dates.

    After future valuation-at-flow boundaries are built, replace this with
    unitization; do not label an approximate daily-return link as exact TWR.
    """
    snaps = list(snapshots)
    if len(snaps) < 2:
        return None
    first, last = snaps[0].as_of, snaps[-1].as_of
    if ledger.filter(kind__in=['DEPOSIT', 'WITHDRAW'], occurred_on__gt=first, occurred_on__lte=last).exists():
        return None
    if snaps[0].total_value <= ZERO:
        return None
    return time_weighted_return([(snaps[0].total_value, snaps[-1].total_value)])
