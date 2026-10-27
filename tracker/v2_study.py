"""Study tab: hypothetical past performance for one strategy. Read-only."""
import math
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from django.http import HttpResponseBadRequest, JsonResponse

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from .strategies.registry import module_for
from .study import FILLS
from .models import StrategyInstance, StudyPrice
from .v2_views import _render

CSS = {"nine_sig": "w2-c1", "strategy": "w2-c1", "hold": "w2-c2", "spy": "w2-c3", "vti": "w2-c5"}
MAX_POINTS = 600
# Keep the Study's short presets; replace 20 years with all available history.
STUDY_FRAMES = (("1w", "1 week", 7), ("1m", "1 month", 30),
                ("ytd", "YTD", None), ("1y", "1 year", 365),
                ("5y", "5 years", 1826), ("10y", "10 years", 3652),
                ("max", "All available", None))


def _number(raw, default, low, high):
    try:
        value = Decimal(str(raw).replace(",", "").strip() or default)
    except (InvalidOperation, ValueError):
        return float(default), True
    if not value.is_finite() or value < low or value > high:
        return float(default), True
    return float(value.quantize(Decimal("0.01"))), False


def _data(engine):
    """Cache the built series until the next download."""
    latest = StudyPrice.objects.order_by("-fetched_at").values_list("fetched_at", flat=True).first()
    key = "study-data:%s:%s:%s" % (engine.TITLE.replace(" ", "-"), getattr(engine, "CURRENCY", "GBP"),
                                   latest.isoformat() if latest else "none")
    data = cache.get(key)
    if data is None:
        data = engine.load_data()
        cache.set(key, data, 3600)
    return data


def _chart(engine, result, scale):
    """SVG polylines for the four series plus money put in."""
    days = result["dates"]
    step = max(1, len(days) // MAX_POINTS)
    index = list(range(0, len(days), step))
    if index[-1] != len(days) - 1:
        index.append(len(days) - 1)
    lines = []
    for name, label in engine.SERIES:
        s = result["series"].get(name)
        if s:
            lines.append((label, CSS[name], [s["values"][i] for i in index]))
    base = result["series"][engine.PRIMARY]
    lines.append(("Money put in", "w2-c4", [base["contributed"][i] for i in index]))
    values = [v for _, _, vs in lines for v in vs if v and v > 0]
    if not values:
        return None
    if scale == "log":
        f = math.log10
        lo, hi = f(min(values)), f(max(values))
    else:
        f = float
        lo, hi = 0.0, max(values)
    span = (hi - lo) or 1.0
    first, last = days[0], days[-1]
    total = max((last - first).days, 1)
    xs = [round((days[i] - first).days / total * 800, 1) for i in index]
    out = []
    for label, css, vs in lines:
        pts = " ".join("%s,%.1f" % (x, 200 - (f(v) - lo) / span * 200)
                       for x, v in zip(xs, vs) if v and v > 0)
        out.append({"label": label, "css": css, "points": pts})
    if scale == "log":
        ticks = [10 ** (lo + span * k / 2) for k in (2, 1, 0)]
    else:
        ticks = [hi, hi / 2, 0]
    from .money import symbol_for
    cur = symbol_for(getattr(engine, "CURRENCY", "GBP"))
    return {"lines": out, "y_labels": [cur + "{:,.0f}".format(t) for t in ticks],
            "ticks": [first + (last - first) * k / 4 for k in range(5)],
            "first": first, "last": last}


def study_for(plugin, currency):
    """The plug-in's Study engine for an account currency, or None.

    ``plugin.study`` is one engine or a {currency: engine} map; an engine
    without a ``CURRENCY`` works in pounds.
    """
    study = plugin.study if plugin else None
    if isinstance(study, dict):
        return study.get(currency)
    if study is not None and getattr(study, "CURRENCY", "GBP") != currency:
        return None
    return study



def _study_payload(study, result):
    """Complete dated hypothetical Study history for interactive inspection.

    The source remains the Study engine, not the live portfolio. Preserve every
    trading close for zooming; the browser alone downsamples drawn SVGs.
    """
    main = result["series"][study.PRIMARY]
    dates = result["dates"]
    rows = []
    for key, label in study.SERIES:
        series = result["series"].get(key)
        if series is not None:
            rows.append({
                "key": key, "label": label, "css": CSS[key],
                "points": [round(value, 2) for value in series["values"]],
            })
    return {
        "ready": True,
        "point_dates_iso": [day.isoformat() for day in dates],
        "put_in_points": [round(x, 2) for x in main["contributed"]],
        "rows": rows,
        "primary": study.PRIMARY,
        "currency": getattr(study, "CURRENCY", "GBP"),
    }


def _cached_window_payload(strategy, study, data, frame, amount, monthly, fill):
    """Bound replays in the per-owner, per-engine, per-price-history cache."""
    # Data cache invalidation is based on fetch timestamp in _data; mirror it
    # here so a newly downloaded price never reuses an older simulation.
    last_fetch = StudyPrice.objects.order_by("-fetched_at").values_list("fetched_at", flat=True).first()
    raw_key = repr((strategy.pk, strategy.strategy_key, study.TITLE,
                    getattr(study, "CURRENCY", "GBP"), str(last_fetch),
                    frame, amount, monthly, fill))
    key = "study-zoom:" + sha256(raw_key.encode("utf-8")).hexdigest()
    value = cache.get(key)
    if value is None:
        result = study.run(data, frame, amount, monthly, fill)
        if result is None:
            return None
        value = _study_payload(study, result)
        cache.set(key, value, 3600)
    return value


@login_required
@require_GET
def study_window_json(request, instance_id):
    """Owner-only selected-period metrics, never an execution recommendation."""
    strategy = get_object_or_404(
        StrategyInstance.objects.select_related("account", "preset").filter(
            owner=request.user, account__owner=request.user
        ).exclude(account__kind="PAPER"), pk=instance_id)
    study = study_for(module_for(strategy), strategy.account.currency)
    if study is None:
        return HttpResponseBadRequest("Study unavailable")
    frame = request.GET.get("range", "10y")
    if frame not in {k for k, _, _ in STUDY_FRAMES}:
        return HttpResponseBadRequest("Unknown Study timeframe")
    amount, bad_amount = _number(request.GET.get("amount", ""), "1000", 1, 10_000_000)
    monthly, bad_monthly = _number(request.GET.get("monthly", ""), "0", 0, 1_000_000)
    fill = request.GET.get("fill", "close")
    if bad_amount or bad_monthly or fill not in ("close", "next"):
        return HttpResponseBadRequest("Invalid Study inputs")
    raw_start, raw_end = request.GET.get("window_start", ""), request.GET.get("window_end", "")
    if (not raw_start.isascii() or not raw_end.isascii()
            or not raw_start.isdecimal() or not raw_end.isdecimal()
            or len(raw_start) > 5 or len(raw_end) > 5):
        return HttpResponseBadRequest("Invalid Study dates")
    data = _data(study)
    if not data.ready:
        return HttpResponseBadRequest("History unavailable")
    payload = _cached_window_payload(strategy, study, data, frame, amount, monthly, fill)
    if payload is None:
        return HttpResponseBadRequest("History unavailable")
    from .v2_home import selected_period_metrics
    start, end = int(raw_start), int(raw_end)
    try:
        metrics = selected_period_metrics(payload, start, end)
    except (ValueError, TypeError, IndexError, KeyError, ArithmeticError):
        return HttpResponseBadRequest("Invalid selected Study dates")
    # Study's selected cash flow and gain, for both comparison table and hero.
    additional = payload["put_in_points"][end] - payload["put_in_points"][start]
    for row in payload["rows"]:
        figures = metrics["rows"][row["key"]]
        invested = row["points"][start] + additional
        figures["put_in"] = round(invested, 2)
        figures["gain"] = round(row["points"][end] - invested, 2)
    return JsonResponse(metrics)


@login_required
@require_GET
def study_simulate_json(request, instance_id):
    """Owner-only what-if on stored Study prices. Hypothetical; never an instruction."""
    strategy = get_object_or_404(
        StrategyInstance.objects.select_related("account", "preset").filter(
            owner=request.user, account__owner=request.user
        ).exclude(account__kind="PAPER"), pk=instance_id)
    plugin = module_for(strategy)
    if plugin is None or plugin.simulate is None:
        return HttpResponseBadRequest("No what-if for this strategy")
    try:
        return JsonResponse(plugin.simulate(request.GET))
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))


@login_required
@require_GET
def study_page(request, instance_id):
    strategy = get_object_or_404(
        StrategyInstance.objects.select_related("account", "preset").filter(
            owner=request.user, account__owner=request.user,
        ).exclude(account__kind="PAPER"), pk=instance_id,
    )
    amount, bad_amount = _number(request.GET.get("amount", ""), "1000", 1, 10_000_000)
    monthly, bad_monthly = _number(request.GET.get("monthly", ""), "0", 0, 1_000_000)
    plugin = module_for(strategy)
    study = study_for(plugin, strategy.account.currency)
    if study is None:
        return _render(request, page_key="instance", page_title="Study", instance=strategy,
                       section="study", study_supported=False, study_title="")
    key = request.GET.get("range", "10y")
    if key not in {k for k, _, _ in STUDY_FRAMES}:
        key = "10y"
    scale = "log" if request.GET.get("scale") == "log" else "linear"
    fill = "next" if request.GET.get("fill") == "next" else "close"
    supported = True
    data = _data(study)
    rows, selected = [], None
    if data.ready:
        for k, label, _ in STUDY_FRAMES:
            result = study.run(data, k, amount, monthly, fill)
            rows.append({"key": k, "label": label, "result": result})
            if k == key:
                selected = result
    best = None
    if selected:
        finals = {n: s["final"] for n, s in selected["series"].items() if s}
        best = max(finals, key=finals.get) if finals else None
    for row in rows:
        if row["result"]:
            finals = {n: s["final"] for n, s in row["result"]["series"].items() if s}
            row["best"] = max(finals, key=finals.get) if finals else None
            row["cells"] = [(n, row["result"]["series"].get(n)) for n, _ in study.SERIES]
    return _render(
        request, page_key="instance", page_title="Study", instance=strategy, section="study",
        study_supported=supported, study_ready=data.ready,
        study_amount=amount, study_monthly=monthly, study_range=key, study_scale=scale,
        study_fill=fill, study_fills=FILLS,
        study_input_error=bad_amount or bad_monthly,
        study_timeframes=STUDY_FRAMES, study_series=study.SERIES,
        study_rows=rows, study_selected=selected, study_best=best,
        study_selected_rows=[(n, label, CSS[n], selected["series"].get(n))
                             for n, label in study.SERIES] if selected else (),
        study_chart=_chart(study, selected, scale) if selected else None,
        study_title=study.TITLE, study_needs=study.NEEDS,
        study_main=selected["series"][study.PRIMARY] if selected else None,
        study_how=study.how_it_works(data),
        study_first_day=data.days[0] if data.ready else None,
        study_last_day=data.days[-1] if data.ready else None,
        study_zoom_data=_study_payload(study, selected) if selected else None,
        study_can_simulate=bool(plugin and plugin.simulate and data.ready),
        study_sim_fields=plugin.simulate_fields if plugin and plugin.simulate else (),
    )
