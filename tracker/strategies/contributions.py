"""Version-neutral allocation of *new* cash; never a scheduled rebalance.

Pure helper shared by live Today and historical Study. No trades, persistence,
broker access or changes to the pinned quarterly/yearly strategy algorithms.
Positions are valued in the account currency with verified prices by caller.
"""
from decimal import Decimal, ROUND_DOWN

ZERO = Decimal("0")
CENT = Decimal("0.01")
RESERVE_STRATEGIES = frozenset(("nasdaq-quarterly-target", "three-sig"))
FIXED_STRATEGIES = frozenset((
    "all-weather", "golden-butterfly", "hfea", "permanent-portfolio",
))


def allocation(key, amount, *, values=None, weights=None, chosen=None):
    """Return {role: cash amount}, no sells, with total never above amount.

    9Sig/3Sig: new money goes to the safe sleeve between quarterly reviews.
    Fixed-weight: distribute to the currently underweight sleeves only.
    Dual Momentum: keep to the last verified month-end chosen sleeve.
    Nasdaq Trend + DCA uses its *own* daily strategy / DCA engine, not here.
    """
    amount = Decimal(str(amount))
    if not amount.is_finite() or amount <= ZERO:
        return {}
    amount = amount.quantize(CENT, rounding=ROUND_DOWN)
    if amount < CENT:
        return {}
    if key in RESERVE_STRATEGIES:
        return {"safe": amount}
    if key == "dual-momentum":
        return {chosen: amount} if chosen in ("us", "intl", "bonds") else {}
    if key not in FIXED_STRATEGIES or not weights:
        return {}
    weights = {role: Decimal(str(w)) for role, w in weights.items()}
    values = {r: Decimal(str(v)) for r, v in (values or {}).items()}
    if (any(not w.is_finite() or w <= ZERO for w in weights.values()) or
            sum(weights.values(), ZERO) != Decimal("1") or
            any(not v.is_finite() or v < ZERO for v in values.values()) or
            any(r not in weights for r in values)):
        return {}
    portfolio = sum(values.values(), ZERO) + amount
    gaps = {r: max(ZERO, portfolio * w - values.get(r, ZERO))
            for r, w in weights.items()}
    wanted = sum(gaps.values(), ZERO)
    if wanted <= ZERO:
        return {}
    budget = min(amount, wanted)
    allocations = {r: (budget * g / wanted).quantize(CENT, rounding=ROUND_DOWN)
                   for r, g in gaps.items() if g > ZERO}
    left = budget - sum(allocations.values(), ZERO)
    if left >= CENT:
        winner = max(gaps, key=lambda r: gaps[r])
        allocations[winner] = allocations.get(winner, ZERO) + left
    return {r: a for r, a in allocations.items() if a >= CENT}
