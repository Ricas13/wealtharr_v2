"""Signed-out homepage: what Wealtharr does, a hypothetical strategy comparison
from the Study history, and an invite request. Nothing here signs anyone up;
the owner reads invite requests and creates any account by hand."""
import logging
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
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
POINTS = 160
DAILY_INVITE_ALERTS = 10


def _snap(raw, default, low, high, step):
    try:
        value = int(float(str(raw).replace(",", "")))
    except (TypeError, ValueError):
        return default
    value = max(low, min(high, value))
    return int(round(value / step) * step)


def _inputs(params):
    frame = params.get("range", "10y")
    return (_snap(params.get("amount"), 1000, 500, 20000, 500),
            _snap(params.get("monthly"), 100, 0, 1000, 25),
            frame if frame in FRAME_WORDS else "10y")


def compare(amount, monthly, frame):
    """Chart-ready hypothetical comparison, cached until the next download."""
    latest = StudyPrice.objects.order_by("-fetched_at").values_list("fetched_at", flat=True).first()
    key = "home-compare:%s:%s:%s:%s" % (latest.isoformat() if latest else "none", amount, monthly, frame)
    out = cache.get(key)
    if out is not None:
        return out
    out = {"ready": False, "amount": amount, "monthly": monthly, "range": frame,
           "range_words": FRAME_WORDS[frame]}
    # The homepage compares the flagship strategy's Study against benchmarks.
    from .instrument_mapping import STRATEGY_9SIG
    from .strategies.registry import module_for_key
    study = module_for_key(STRATEGY_9SIG).study
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
            rows.append({
                "key": name, "label": label, "what": what, "effort": effort, "color": color,
                "final": round(s["final"], 2), "put_in": round(s["put_in"], 2),
                "gain_pct": round(s["gain_pct"], 2) if s["gain_pct"] is not None else None,
                "annual": round(s["annual"], 2) if s["annual"] is not None else None,
                "drawdown": round(s["drawdown"], 2),
                "points": [round(s["values"][i], 2) for i in index],
            })
        base = result["series"]["nine_sig"]
        first, last = days[0], days[-1]
        out.update({
            "ready": True, "rows": rows,
            "put_in": round(base["put_in"], 2),
            "put_in_points": [round(base["contributed"][i], 2) for i in index],
            "start": first.isoformat(), "end": last.isoformat(),
            "ticks": [(first + (last - first) * k / 4).strftime("%b %Y") for k in range(5)],
            "simulated": bool(result["simulated_growth"] or result["simulated_safe"]),
        })
    cache.set(key, out, 3600)
    return out


def landing(request):
    if request.user.is_authenticated:
        return redirect(preferred_url(request.user))
    amount, monthly, frame = _inputs(request.GET)
    initial = compare(amount, monthly, frame)
    return render(request, "v2/home.html", {
        "compare": initial, "frames": FRAMES,
        "invite_state": request.GET.get("invite", ""),
        "turnstile_site_key": settings.TURNSTILE_SITE_KEY,
        "turnstile_required": settings.TURNSTILE_REQUIRED,
    })


@require_GET
def compare_json(request):
    return JsonResponse(compare(*_inputs(request.GET)))


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
