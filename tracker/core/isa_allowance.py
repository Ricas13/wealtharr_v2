"""Advisory UK ISA subscription arithmetic, without broker or tax-account access.

The known total counts recorded DEPOSITS only. Withdrawals are *not* deducted:
flexible-ISA replacement treatment requires evidence Wealtharr does not hold.
Other ISA subscriptions and ISA-to-ISA transfers are not known to this app.
Do not use this estimate to approve or block a subscription.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# HMRC: £20,000 overall ISA subscription limit for tax year 2026/27;
# reviewed 2026-10-19: https://www.gov.uk/individual-savings-accounts
# Revision needed if legislation changes; this is advisory, not a tax engine.
OVERALL_LIMIT_2026_GBP = Decimal("20000.00")
CAUTION_THRESHOLD = Decimal("0.80")


@dataclass(frozen=True)
class IsaAllowanceEstimate:
    tax_year_label: str
    tax_year_start: date
    tax_year_end: date
    recorded_deposits_gbp: Decimal
    other_isa_subscriptions_gbp: Decimal | None
    known_total_gbp: Decimal
    remaining_if_complete_gbp: Decimal | None
    status: str  # limit_reached, approaching, partial, unverified
    warning: str


def tax_year_bounds(when: date) -> tuple[date, date]:
    if type(when) is not date:
        raise ValueError("ISA tax-year calculation requires a calendar date.")
    start_year = when.year if (when.month, when.day) >= (4, 6) else when.year - 1
    return date(start_year, 4, 6), date(start_year + 1, 4, 5)


def estimate_allowance(*, as_of: date, recorded_deposits: tuple,
                       other_isa_subscriptions: Decimal | None = None
                       ) -> IsaAllowanceEstimate:
    start, end = tax_year_bounds(as_of)
    # Avoid giving a possibly outdated statutory limit for other tax years.
    if start.year != 2026:
        raise ValueError("ISA limit policy is not verified for this tax year.")
    if other_isa_subscriptions is not None and (
            not isinstance(other_isa_subscriptions, Decimal) or
            not other_isa_subscriptions.is_finite() or
            other_isa_subscriptions < 0):
        raise ValueError("Other ISA subscriptions must be a non-negative GBP amount.")
    recorded = Decimal("0")
    for occurred_on, amount in recorded_deposits:
        if type(occurred_on) is not date or not isinstance(amount, Decimal):
            raise ValueError("Invalid recorded deposit evidence.")
        if not amount.is_finite() or amount <= 0:
            raise ValueError("Invalid recorded deposit amount.")
        if start <= occurred_on <= end:
            recorded += amount
    known = recorded + (other_isa_subscriptions or Decimal("0"))
    label = f"{start.year}/{str(end.year)[-2:]}"
    gap = OVERALL_LIMIT_2026_GBP - known
    if known > OVERALL_LIMIT_2026_GBP:
        state = "limit_reached"
        detail = ("The recorded amounts exceed the £20,000 overall ISA allowance. "
                  "Check the broker and HMRC before adding any more funds.")
    elif known == OVERALL_LIMIT_2026_GBP:
        state = "limit_reached"
        detail = ("The recorded amounts reach the £20,000 overall ISA allowance. "
                  "Check all ISAs before contributing again.")
    elif known >= OVERALL_LIMIT_2026_GBP * CAUTION_THRESHOLD:
        state = "approaching"
        detail = ("Recorded amounts are approaching the £20,000 ISA allowance. "
                  "Verify your other ISAs before contributing.")
    elif other_isa_subscriptions is None:
        state = "partial"
        detail = ("This tracks deposits recorded in this Wealtharr ISA only. "
                  "Other ISA subscriptions are unknown.")
    else:
        state = "unverified"
        detail = ("This estimate includes the amount you supplied for other ISAs, "
                  "but Wealtharr cannot verify those subscriptions.")
    return IsaAllowanceEstimate(
        tax_year_label=label, tax_year_start=start, tax_year_end=end,
        recorded_deposits_gbp=recorded,
        other_isa_subscriptions_gbp=other_isa_subscriptions,
        known_total_gbp=known,
        remaining_if_complete_gbp=max(gap, Decimal("0"))
            if other_isa_subscriptions is not None else None,
        status=state,
        warning=(detail + " Withdrawals, flexible ISA replacements and transfers "
                 "need manual HMRC/broker reconciliation. This is advisory, "
                 "not proof of your remaining allowance.")
    )
