"""Five-step, server-rendered owner onboarding for a fixed strategy.

Only released catalogue combinations can be created. An owner-reported
opening holding is NOT a fabricated execution or price, and never enables
automatic market instructions. All creation is atomic.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .core.catalogue import (
    OFFERS, PLATFORMS, ACCOUNT_TYPES, STRATEGY_9SIG,
    CATALOGUE_VERSION, availability_reason, onboarding_manifest,
    released_version, rows_for, selection_available,
)
from .core.isa_allowance import OVERALL_LIMIT_2026_GBP, tax_year_bounds
from .models import (
    Account, InstanceOpeningSnapshot, LedgerEntry, StrategyInstance,
    StrategyPreset,
)
from .services import validate_and_record


SESSION_KEY = "wealtharr_v2_onboarding"
ALERT_TYPES = (
    ("quarterly", "Quarter-end action"),
    ("urgent", "Urgent mid-period warning"),
    ("unacted", "Reminder if I haven't acted"),
    ("contribution", "Contribution reminder"),
    ("data", "Data problem near review"),
)
CHANNELS = ("email", "discord")


def _new_state(owner_id):
    return {"owner_id": owner_id, "step": 1, "strategy": "", "platform": "TRADING212",
            "type": "", "ack": False, "start": "", "name": "", "mode": "fresh",
            "amount": "", "cash": "", "holdings": {}}


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


def _isa_recorded(owner, as_of):
    start, end = tax_year_bounds(as_of)
    amount = (LedgerEntry.objects.filter(
        strategy__owner=owner, strategy__account__kind="ISA",
        kind=LedgerEntry.DEPOSIT, occurred_on__range=(start, end),
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0"))
    return amount, start.year == 2026


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
            cost = _decimal(post.get("cost_" + line.role, ""), line.role + " average price", precision=8)
            if shares > 0 and cost <= 0:
                raise ValidationError(f"Enter the actual average cost for {line.role} shares.")
            if shares == 0 and cost != 0:
                raise ValidationError(f"Remove the average cost when {line.role} shares are zero.")
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


def _routes(post):
    return {
        kind: {channel: post.get(f"alert_{kind}_{channel}") == "on"
               for channel in CHANNELS}
        for kind, _ in ALERT_TYPES
    }


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
            equity_symbol=instruments["growth"].ticker,
            safe_symbol=instruments["safe"].ticker,
            owner_checked_on=timezone.localdate(),
            tracking_only=True, market_data_enabled=False,
            onboarding_mode=state["mode"], alert_routes=state["routes"],
        )
        if state["mode"] == "fresh":
            actual_amount = _decimal(state["amount"], "deposited amount", allow_zero=False)
            validate_and_record(instance, LedgerEntry(
                kind=LedgerEntry.DEPOSIT, amount=actual_amount, occurred_on=start,
                note="Actual deposit entered during v2 onboarding.",
            ))
        else:
            InstanceOpeningSnapshot.objects.create(
                strategy=instance, reported_on=start,
                cash_gbp=_decimal(state["cash"], "opening cash"),
                positions=state["holdings"],
            )
    return instance


@require_http_methods(["GET", "POST"])
@login_required
def wizard(request):
    from .v2_views import _render

    state = request.session.get(SESSION_KEY)
    if (not isinstance(state, dict) or state.get("owner_id") != request.user.pk
            or request.GET.get("restart") == "1"):
        state = _new_state(request.user.pk)
        request.session[SESSION_KEY] = state
    error = ""
    if request.method == "POST":
        action = request.POST.get("direction", "next")
        try:
            if action == "back":
                state["step"] = max(1, state["step"] - 1)
            elif request.POST.get("step") != str(state["step"]):
                raise ValidationError("Form step changed. Review the current step.")
            elif state["step"] == 1:
                key = request.POST.get("strategy", "")
                if key not in OFFERS or not OFFERS[key].enabled:
                    raise ValidationError("This strategy is not available yet.")
                state.update(strategy=key, type="", ack=False, name="", step=2)
            elif state["step"] == 2:
                platform = request.POST.get("platform", "")
                account_type = request.POST.get("account_type", "")
                if not selection_available(state["strategy"], platform, account_type):
                    raise ValidationError(availability_reason(state["strategy"], platform, account_type))
                state.update(platform=platform, type=account_type, ack=False, step=3)
            elif state["step"] == 3:
                if request.POST.get("ack") != "yes":
                    raise ValidationError("Confirm you checked these instruments in your actual account.")
                state.update(ack=True, step=4)
            elif state["step"] == 4:
                info = _validated_start(request.POST, state)
                state.update(info)
                state["step"] = 5
            elif state["step"] == 5:
                state["routes"] = _routes(request.POST)
                instance = _enroll(request.user, state)
                del request.session[SESSION_KEY]
                return redirect("v2_instance_page", instance_id=instance.pk, section="today")
            request.session[SESSION_KEY] = state
        except (ValidationError, IntegrityError, ValueError, ArithmeticError) as exc:
            error = exc.messages[0] if isinstance(exc, ValidationError) else "Cannot save these details safely. No partial account was created."
    lines = rows_for(
        state.get("strategy", ""), state.get("platform", ""), state.get("type", "")
    )
    known_isa, verified_year = _isa_recorded(request.user, timezone.localdate())
    duplicate = bool(state.get("type") and StrategyInstance.objects.filter(
        owner=request.user, preset__slug=state.get("strategy"),
        account__kind=state.get("type"), account__broker=state.get("platform"),
    ).exists())
    account_options = [
        {"key": kind, "name": title,
         "enabled": selection_available(state.get("strategy", ""),
                                        state.get("platform", ""), kind),
         "reason": availability_reason(state.get("strategy", ""),
                                       state.get("platform", ""), kind)}
        for kind, title in ACCOUNT_TYPES
    ]
    for option in account_options:
        option["duplicate"] = StrategyInstance.objects.filter(
            owner=request.user, preset__slug=state.get("strategy"),
            account__kind=option["key"], account__broker=state.get("platform"),
        ).exists()
    hold_rows = [
        {"line": line,
         "shares": state.get("holdings", {}).get(line.role, {}).get("shares", ""),
         "cost": state.get("holdings", {}).get(line.role, {}).get("average_cost_gbp", "")}
        for line in lines
    ]
    defaults = {
        "quarterly": (True, True), "urgent": (True, True),
        "unacted": (True, False), "contribution": (False, False),
        "data": (True, True),
    }
    route_rows = [
        {"key": kind, "label": label,
         "email": state.get("routes", {}).get(kind, {}).get("email", defaults[kind][0]),
         "discord": state.get("routes", {}).get(kind, {}).get("discord", defaults[kind][1])}
        for kind, label in ALERT_TYPES
    ]
    return _render(
        request, page_key="add", page_title="Add strategy",
        wizard=state, wizard_error=error, wizard_offers=OFFERS.values(),
        wizard_platforms=PLATFORMS, wizard_types=ACCOUNT_TYPES,
        wizard_lines=lines, wizard_duplicate=duplicate,
        wizard_alerts=ALERT_TYPES, wizard_channels=CHANNELS,
        wizard_known_isa=known_isa, wizard_isa_limit=OVERALL_LIMIT_2026_GBP,
        wizard_tax_year_verified=verified_year,
        wizard_account_options=account_options, wizard_hold_rows=hold_rows,
        wizard_route_rows=route_rows,
    )
