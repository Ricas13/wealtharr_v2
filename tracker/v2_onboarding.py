"""One-screen, server-rendered owner onboarding for a fixed strategy.

Only released catalogue combinations can be created. An owner-reported
opening holding is NOT a fabricated execution or price, and never enables
automatic market instructions. All creation is atomic.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .core.catalogue import (
    OFFERS, PLATFORMS, ACCOUNT_TYPES,
    CATALOGUE_VERSION, availability_reason, onboarding_manifest,
    released_version, rows_for, selection_available,
)
from .core.isa_allowance import OVERALL_LIMIT_2026_GBP, tax_year_bounds
from .models import (
    Account, LedgerEntry, StrategyInstance,
    StrategyPreset,
)
from .services import validate_and_record


def _decimal(value, label, *, precision=2, allow_zero=True):
    raw = str(value).strip()
    if not raw:
        if allow_zero:
            return Decimal("0")
        raise ValidationError(f"Enter {label}.")
    try:
        result = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ValidationError(f"{label} must be a valid decimal.")
    quantum = Decimal(1).scaleb(-precision)
    if (not result.is_finite() or result < 0 or
            result >= Decimal("100000000000") or result != result.quantize(quantum)):
        raise ValidationError(f"{label} must be a non-negative number with at most {precision} decimal places.")
    if not allow_zero and result == 0:
        raise ValidationError(f"{label} must be greater than zero.")
    return result


def _date(value):
    try:
        parsed = date.fromisoformat(str(value))
    except (ValueError, TypeError):
        raise ValidationError("Enter a valid start date.")
    if parsed > timezone.localdate():
        raise ValidationError("The start date cannot be in the future.")
    if parsed.year < 1900:
        raise ValidationError("Enter the actual strategy start date.")
    return parsed


OPENING_NOTE = "Opening position you entered (not a broker trade)."


def isa_deposits(owner, start, end):
    """Real deposits into the owner's ISA strategies in a tax year, after corrections."""
    from .v2_ledger_core import effective_entries
    return tuple(
        (e.occurred_on, e.amount)
        for s in StrategyInstance.objects.filter(owner=owner, account__kind="ISA")
        for e in effective_entries(s)
        if e.kind == LedgerEntry.DEPOSIT and start <= e.occurred_on <= end and e.note != OPENING_NOTE)


def _isa_recorded(owner, as_of):
    start, end = tax_year_bounds(as_of)
    amount = sum((amount for _, amount in isa_deposits(owner, start, end)), Decimal("0"))
    return amount, start.year == 2026


def _draft_start(post, state):
    """What the owner typed on step 4, kept so a validation error doesn't wipe it."""
    return {
        "name": post.get("name", "").strip()[:100],
        "start": post.get("start", "").strip()[:10],
        "mode": post.get("mode", "") if post.get("mode") in ("fresh", "hold") else state.get("mode", "fresh"),
        "amount": post.get("amount", "").strip()[:20],
        "cash": post.get("cash", "").strip()[:20],
        "holdings": {
            line.role: {"shares": post.get("shares_" + line.role, "").strip()[:30],
                        "average_cost_gbp": post.get("cost_" + line.role, "").strip()[:30]}
            for line in rows_for(state["strategy"], state["platform"], state["type"])
        },
    }


def _validated_start(post, state):
    name = post.get("name", "").strip()
    if not name or len(name) > 100:
        raise ValidationError("Enter a name (up to 100 characters).")
    start = _date(post.get("start", ""))
    mode = post.get("mode", "")
    if mode not in ("fresh", "hold"):
        raise ValidationError("Choose starting fresh or already holding.")
    result = {"name": name, "start": start.isoformat(), "mode": mode,
              "amount": "", "cash": "", "holdings": {}}
    if mode == "fresh":
        amount = _decimal(post.get("amount", ""), "your actual deposited amount", allow_zero=False)
        if post.get("deposited") != "yes":
            raise ValidationError("Confirm this money was actually deposited. Planned deposits are not recorded.")
        result["amount"] = str(amount)
    else:
        cash = _decimal(post.get("cash", ""), "remaining cash")
        result["cash"] = str(cash)
        any_holding = False
        for line in rows_for(state["strategy"], state["platform"], state["type"]):
            shares = _decimal(post.get("shares_" + line.role, ""), line.role + " shares", precision=8)
            cost = _decimal(post.get("cost_" + line.role, ""), line.role + " price per share", precision=8)
            if shares > 0 and cost <= 0:
                raise ValidationError(f"Enter the {line.role} price per share on your start date.")
            if shares == 0 and cost != 0:
                raise ValidationError(f"Remove the {line.role} price when you hold no {line.role} shares.")
            if shares > 0:
                any_holding = True
            result["holdings"][line.role] = {
                "ticker": line.ticker,
                "isin": line.isin,
                "shares": str(shares),
                "average_cost_gbp": str(cost),
            }
        if not any_holding and cash == 0:
            raise ValidationError("Enter some existing shares or an actual cash balance.")
    return result



def _unique_account_name(owner, display, kind):
    label = (display + " · " + kind)[:75]
    existing = set(Account.objects.filter(owner=owner).values_list("name", flat=True))
    for index in range(1, 10001):
        candidate = label if index == 1 else (label[:70] + " #" + str(index))
        if candidate not in existing:
            return candidate
    raise ValidationError("Too many accounts have this name.")


def _enroll(owner, state):
    strategy = state["strategy"]
    platform = state["platform"]
    account_type = state["type"]
    if not selection_available(strategy, platform, account_type):
        raise ValidationError("Instrument mapping is unavailable. Nothing was created.")
    if not state["ack"]:
        raise ValidationError("Confirm the actual instruments in your own account.")
    start = _date(state["start"])
    name = state["name"]
    if not name or len(name) > 100:
        raise ValidationError("Invalid strategy name.")
    manifest = onboarding_manifest(strategy, platform, account_type)
    lines = rows_for(strategy, platform, account_type)
    if not lines or not all(row.status != "draft" for row in lines):
        raise ValidationError("An instrument is not reviewed; cannot create this strategy.")
    with transaction.atomic():
        preset, _ = StrategyPreset.objects.get_or_create(
            slug=strategy,
            defaults={
                "name": OFFERS[strategy].title, "risk": "HIGH",
                "summary": OFFERS[strategy].explanation, "active": True,
            },
        )
        account = Account.objects.create(
            owner=owner, name=_unique_account_name(owner, name, account_type),
            country="GB", kind=account_type, broker=platform,
        )
        instruments = {line.role: line for line in lines}
        instance = StrategyInstance.objects.create(
            owner=owner, account=account, preset=preset, name=name,
            strategy_key=strategy, platform_code=platform,
            account_type_code=account_type, started_on=start,
            strategy_version=released_version(strategy),
            mapping_version=CATALOGUE_VERSION, resolved_instruments=manifest,
            # Legacy two-column fields; every role lives in resolved_instruments.
            equity_symbol=lines[0].ticker,
            safe_symbol=lines[1].ticker if len(lines) > 1 else "",
            owner_checked_on=timezone.localdate(),
            tracking_only=True, market_data_enabled=False,
            onboarding_mode=state["mode"], alert_routes=state["routes"],
            monthly_target=state.get("monthly") or Decimal("0"),
        )
        if state["mode"] == "fresh":
            actual_amount = _decimal(state["amount"], "deposited amount", allow_zero=False)
            validate_and_record(instance, LedgerEntry(
                kind=LedgerEntry.DEPOSIT, amount=actual_amount, occurred_on=start,
                note="Actual deposit entered during v2 onboarding.",
            ))
        else:
            # The holdings the owner entered become the opening position: one
            # funding entry for their value plus cash, and one buy per holding
            # at the start-date price. Labelled as owner-entered, not broker trades.
            cash = _decimal(state["cash"], "opening cash")
            buys = []
            for role, holding in state["holdings"].items():
                shares = Decimal(holding["shares"])
                price = Decimal(holding["average_cost_gbp"])
                if shares > 0:
                    buys.append(LedgerEntry(
                        kind=LedgerEntry.BUY, symbol=instruments[role].ticker,
                        quantity=shares, unit_price=price, occurred_on=start, note=OPENING_NOTE,
                    ))
            funding = cash + sum(((b.quantity * b.unit_price).quantize(Decimal("0.01")) for b in buys), Decimal("0"))
            if funding > 0:
                validate_and_record(instance, LedgerEntry(
                    kind=LedgerEntry.DEPOSIT, amount=funding, occurred_on=start, note=OPENING_NOTE,
                ))
            for buy in buys:
                validate_and_record(instance, buy)
    return instance


ROUTE_DEFAULTS = {
    "quarterly": {"email": True, "discord": True}, "urgent": {"email": True, "discord": True},
    "unacted": {"email": True, "discord": False}, "contribution": {"email": False, "discord": False},
    "data": {"email": True, "discord": True},
}


@require_http_methods(["GET", "POST"])
@login_required
def wizard(request):
    """Everything on one page: strategy, account, instruments, starting point, go live."""
    from .v2_views import _render

    offers = [o for o in OFFERS.values()]
    default_key = next((o.key for o in offers if o.enabled), "")
    post = request.POST if request.method == "POST" else {}
    chosen = request.GET.get("strategy", "")
    strategy = post.get("strategy", chosen if chosen in OFFERS and OFFERS[chosen].enabled else default_key)
    platform = post.get("platform", "TRADING212")
    types = [kind for kind, _ in ACCOUNT_TYPES if selection_available(strategy, platform, kind)]
    account_type = post.get("account_type", "ISA" if "ISA" in types else (types[0] if types else ""))
    state = {"owner_id": request.user.pk, "strategy": strategy, "platform": platform,
             "type": account_type, "ack": post.get("ack") == "yes",
             "mode": post.get("mode", "fresh"), "name": "", "start": "",
             "amount": "", "cash": "", "holdings": {}, "routes": ROUTE_DEFAULTS}
    error = ""
    if request.method == "POST":
        try:
            if strategy not in OFFERS or not OFFERS[strategy].enabled:
                raise ValidationError("This strategy is not available yet.")
            if not selection_available(strategy, platform, account_type):
                raise ValidationError(availability_reason(strategy, platform, account_type))
            state.update(_draft_start(post, state))
            state.update(_validated_start(post, state))
            state["monthly"] = _decimal(post.get("monthly", ""), "your monthly top-up")
            if not state["ack"]:
                raise ValidationError("Tick the box to confirm you checked these instruments in your own account.")
            instance = _enroll(request.user, state)
            if post.get("go_live") == "yes":
                from .v2_go_live import go_live
                try:
                    go_live(instance, request.user)
                except ValidationError as exc:
                    messages.error(request, "Created, but not live yet: " + " ".join(exc.messages))
            messages.success(request, f"{instance.name} is set up.")
            return redirect("v2_instance_page", instance_id=instance.pk, section="today")
        except (ValidationError, IntegrityError, ValueError, ArithmeticError) as exc:
            error = exc.messages[0] if isinstance(exc, ValidationError) else "Cannot save these details safely. Nothing was created."
    lines = rows_for(strategy, platform, account_type) if account_type else ()
    known_isa, verified_year = _isa_recorded(request.user, timezone.localdate())
    duplicate = bool(account_type and StrategyInstance.objects.filter(
        owner=request.user, preset__slug=strategy,
        account__kind=account_type, account__broker=platform,
    ).exists())
    hold_rows = [
        {"line": line,
         "shares": state["holdings"].get(line.role, {}).get("shares", ""),
         "cost": state["holdings"].get(line.role, {}).get("average_cost_gbp", "")}
        for line in lines
    ]
    return _render(
        request, page_key="add", page_title="Add strategy",
        wizard=state, wizard_error=error, wizard_offers=offers,
        wizard_platforms=PLATFORMS,
        wizard_account_options=[{"key": kind, "name": title, "enabled": kind in types}
                                for kind, title in ACCOUNT_TYPES],
        wizard_lines=lines, wizard_duplicate=duplicate, wizard_hold_rows=hold_rows,
        wizard_known_isa=known_isa, wizard_isa_limit=OVERALL_LIMIT_2026_GBP,
        wizard_tax_year_verified=verified_year,
        wizard_today=timezone.localdate(),
        wizard_amount=post.get("amount", ""), wizard_deposited=post.get("deposited") == "yes",
        wizard_monthly=post.get("monthly", ""),
        wizard_go_live=post.get("go_live") == "yes" or request.method == "GET",
    )
