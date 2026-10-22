"""Signed-out homepage: what Wealtharr does, a hypothetical strategy comparison
from the Study history, and an invite request. Nothing here signs anyone up;
the owner reads invite requests and creates any account by hand."""
import logging
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import InviteRequest, StudyPrice
from .services import dispatch
from .v2_auth import _turnstile_verified, preferred_url
from .v2_study import _data

log = logging.getLogger(__name__)

FRAMES = (("ytd", "YTD"), ("1y", "1Y"), ("5y", "5Y"), ("10y", "10Y"), ("20y", "20Y"))
FRAME_WORDS = {"ytd": "this year", "1y": "1 year", "5y": "5 years", "10y": "10 years", "20y": "20 years"}
LINES = {
    "nine_sig": ("Quarterly signal", "60/40, 9% growth target", "4× a year", "#5b9cf5"),
    "hold": ("Buy & hold 3× Nasdaq", "100% growth fund", "Never", "#f0a050"),
    "spy": ("S&P 500 (SPY)", "benchmark", "Never", "#9aa3ad"),
    "vti": ("US total market (VTI)", "benchmark", "Never", "#b58cf0"),
}
# The other released strategies, by plug-in key. Generic names only: the
# public homepage doesn't use third-party strategy brands (CLAUDE.md).
OTHER_LINES = {
    "all-weather": ("All-season mix", "30% shares, 55% bonds, 15% gold & commodities", "1× a year", "#4fc3a1"),
    "golden-butterfly": ("Five equal parts", "shares, small-cap value, long & short bonds, gold",
                         "1× a year", "#e06c9f"),
    "dual-momentum": ("Monthly momentum switch", "US shares, world shares or bonds", "Up to 12× a year",
                      "#e5c07b"),
    "permanent-portfolio": ("Four equal parts, banded", "shares, long bonds, cash, gold; 15–35% bands",
                            "Rarely", "#56b6c2"),
    "hfea": ("3× shares + 3× bonds", "55% 3× S&P 500, 45% 3× Treasuries", "4× a year", "#c678dd"),
    "three-sig": ("Quarterly signal, small companies", "80/20, 3% growth target", "4× a year", "#98c379"),
}
# Signed-in pages (Add strategy) may use the strategies' own names; the public
# homepage keeps the generic ones above.
REAL_NAMES = {"nine_sig": "9Sig", "hold": "Buy & hold 3× Nasdaq (LQQ3)"}
POINTS = 160
DAILY_INVITE_ALERTS = 10


def _snap(raw, default, low, high, step):
    try:
        value = int(float(str(raw).replace(",", "")))
    except (TypeError, ValueError):
        return default
    value = max(low, min(high, value))
    return int(round(value / step) * step)


# Typed amounts, whole pounds.
LIMITS = {"amount_min": 100, "amount_max": 10_000_000, "monthly_max": 1_000_000}


def _inputs(params):
    frame = params.get("range", "10y")
    return (_snap(params.get("amount"), 1000, LIMITS["amount_min"], LIMITS["amount_max"], 1),
            _snap(params.get("monthly"), 100, 0, LIMITS["monthly_max"], 1),
            frame if frame in FRAME_WORDS else "10y")


def _label(key, generic, real_names):
    """A row's label: generic on the public homepage, the strategy's own name when signed in."""
    if not real_names:
        return generic
    if key in REAL_NAMES:
        return REAL_NAMES[key]
    from .strategies.registry import describe_strategy
    description = describe_strategy(key)
    return description.title if description else generic


# Signed-out visitors can ask for any amount, and each new one is a fresh
# replay of every Study. Cap how many fresh replays they can start per minute
# (per web process) so the public page can't be used to tie up the server.
PUBLIC_FRESH_PER_MINUTE = 30
DEFAULT_INPUTS = (1000, 100, "10y")


class Busy(Exception):
    pass


def _public_budget():
    import time
    window = int(time.time() // 60)
    key = "home-compare-fresh:%d" % window
    cache.add(key, 0, 120)
    try:
        used = cache.incr(key)
    except ValueError:
        used = 1
    if used > PUBLIC_FRESH_PER_MINUTE:
        raise Busy()


def compare(amount, monthly, frame, real_names=False, public=False):
    """Chart-ready hypothetical comparison, cached until the next download."""
    latest = StudyPrice.objects.order_by("-fetched_at").values_list("fetched_at", flat=True).first()
    key = "home-compare:%s:%s:%s:%s:%s" % (latest.isoformat() if latest else "none", amount, monthly, frame,
                                         "real" if real_names else "generic")
    out = cache.get(key)
    if out is not None:
        return out
    if public and (amount, monthly, frame) != DEFAULT_INPUTS:
        _public_budget()
    out = {"ready": False, "amount": amount, "monthly": monthly, "range": frame,
           "range_words": FRAME_WORDS[frame], "limits": LIMITS}
    # The homepage compares the flagship strategy's Study against benchmarks.
    from .instrument_mapping import STRATEGY_9SIG
    from .strategies.registry import module_for_key
    study = module_for_key(STRATEGY_9SIG).study["GBP"]
    data = _data(study)
    result = study.run(data, frame, amount, monthly) if data.ready else None
    if result:
        days = result["dates"]
        step = max(1, -(-len(days) // POINTS))
        index = list(range(0, len(days), step))
        if index[-1] != len(days) - 1:
            index.append(len(days) - 1)
        rows = []
        for name, _ in study.SERIES:
            s = result["series"].get(name)
            if not s:
                continue
            label, what, effort, color = LINES[name]
            label = _label(name, label, real_names)
            rows.append({
                "key": name, "label": label, "what": what, "effort": effort, "color": color,
                "final": round(s["final"], 2), "put_in": round(s["put_in"], 2),
                "gain_pct": round(s["gain_pct"], 2) if s["gain_pct"] is not None else None,
                "annual": round(s["annual"], 2) if s["annual"] is not None else None,
                "drawdown": round(s["drawdown"], 2),
                "points": [round(s["values"][i], 2) for i in index],
            })
        simulated_other, left_out = _other_rows(rows, days, index, frame, amount, monthly, real_names)
        base = result["series"]["nine_sig"]
        first, last = days[0], days[-1]
        out.update({
            "ready": True, "rows": rows,
            "put_in": round(base["put_in"], 2),
            "put_in_points": [round(base["contributed"][i], 2) for i in index],
            "start": first.isoformat(), "end": last.isoformat(),
            "period": "%s – %s" % (_day(first), _day(last)),
            "left_out": left_out,
            "ticks": [(first + (last - first) * k / 4).strftime("%b %Y") for k in range(5)],
            "point_dates": [_day(days[i]) for i in index],
            "simulated": bool(result["simulated_growth"] or result["simulated_safe"]),
            "simulated_other": simulated_other,
        })
    cache.set(key, out, 3600)
    return out


def _day(day):
    return "%d %s" % (day.day, day.strftime("%b %Y"))


def _other_rows(rows, days, index, frame, amount, monthly, real_names=False):
    """Add each other strategy's pound Study to the comparison, on the same dates.

    Returns (whether any of them used stand-in history before a fund existed,
    labels of strategies left out). Only a Study that starts and ends on the
    same days as the others is compared, so every figure covers one period; a
    strategy whose fresh history doesn't cover it is left out and named.
    """
    from .strategies.registry import module_for_key
    simulated, left_out = False, []
    for key, (label, what, effort, color) in OTHER_LINES.items():
        label = _label(key, label, real_names)
        plugin = module_for_key(key)
        study = plugin.study.get("GBP") if plugin and isinstance(plugin.study, dict) else None
        if study is None:
            continue
        data = _data(study)
        result = study.run(data, frame, amount, monthly) if data.ready else None
        s = result["series"].get("strategy") if result else None
        if not s or result["start"] != days[0] or result["end"] != days[-1]:
            left_out.append(label)
            continue
        simulated = simulated or bool(result["simulated_notes"])
        position = {day: i for i, day in enumerate(result["dates"])}
        if any(days[i] not in position for i in index):
            left_out.append(label)  # its trading days differ from the others'
            continue
        rows.append({
            "key": key, "label": label, "what": what, "effort": effort, "color": color,
            "final": round(s["final"], 2), "put_in": round(s["put_in"], 2),
            "gain_pct": round(s["gain_pct"], 2) if s["gain_pct"] is not None else None,
            "annual": round(s["annual"], 2) if s["annual"] is not None else None,
            "drawdown": round(s["drawdown"], 2),
            "points": [round(s["values"][position[days[i]]], 2) for i in index],
        })
    return simulated, left_out


def landing(request):
    if request.user.is_authenticated:
        return redirect(preferred_url(request.user))
    amount, monthly, frame = _inputs(request.GET)
    try:
        initial = compare(amount, monthly, frame, public=True)
    except Busy:
        initial = compare(*DEFAULT_INPUTS)
    return render(request, "v2/home.html", {
        "compare": initial, "frames": FRAMES,
        "invite_state": request.GET.get("invite", ""),
        "turnstile_site_key": settings.TURNSTILE_SITE_KEY,
        "turnstile_required": settings.TURNSTILE_REQUIRED,
    })


@require_GET
def compare_json(request):
    try:
        return JsonResponse(compare(*_inputs(request.GET), public=True))
    except Busy:
        return JsonResponse({"error": "Busy. Try again in a minute."}, status=429)


@login_required
@require_GET
def compare_named_json(request):
    """The same comparison with the strategies' own names, for signed-in pages."""
    return JsonResponse(compare(*_inputs(request.GET), real_names=True))


class InviteForm(forms.Form):
    email = forms.EmailField(max_length=254)
    website = forms.CharField(required=False)  # Left empty by people; filled by bots.


def _notify_owners(invite):
    since = timezone.now() - timedelta(days=1)
    if InviteRequest.objects.filter(created_at__gte=since, notified=True).count() >= DAILY_INVITE_ALERTS:
        return
    owners = get_user_model().objects.filter(is_superuser=True, is_active=True)
    delivered = False
    for owner in owners:
        try:
            outcomes = dispatch(owner, "invite:%d" % invite.pk, "Wealtharr invite request",
                                "%s asked for an invite on the homepage. If you want to let them in, "
                                "create their account in admin." % invite.email)
        except Exception:  # An alert problem never loses the request itself.
            log.exception("Invite notification failed")
            continue
        # Sent now, or queued for the scheduler's retry; "disabled" sends nothing.
        delivered = delivered or any(status in ("sent", "pending", "retrying")
                                     for status in (outcomes or {}).values())
    if delivered:
        InviteRequest.objects.filter(pk=invite.pk).update(notified=True)


@require_POST
def request_invite(request):
    if request.user.is_authenticated:
        return redirect("home")
    form = InviteForm(request.POST)
    if not form.is_valid():
        return redirect("/?invite=invalid#invite")
    if form.cleaned_data["website"] or not _turnstile_verified(request):
        # Same reply as success, so bots learn nothing.
        return redirect("/?invite=thanks#invite")
    email = form.cleaned_data["email"].strip().lower()
    invite, created = InviteRequest.objects.get_or_create(email=email)
    if created:
        _notify_owners(invite)
    return redirect("/?invite=thanks#invite")
