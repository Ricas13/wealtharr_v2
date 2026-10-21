"""Study tab: hypothetical past performance for one strategy. Read-only."""
import math
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from .strategies.registry import module_for
from .models import StrategyInstance, StudyPrice
from .v2_views import _render

CSS = {"nine_sig": "w2-c1", "strategy": "w2-c1", "hold": "w2-c2", "spy": "w2-c3", "vti": "w2-c5"}
MAX_POINTS = 600


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
    if key not in dict((k, l) for k, l, _ in study.TIMEFRAMES):
        key = "10y"
    scale = "log" if request.GET.get("scale") == "log" else "linear"
    supported = True
    data = _data(study)
    rows, selected = [], None
    if data.ready:
        for k, label, _ in study.TIMEFRAMES:
            result = study.run(data, k, amount, monthly)
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
        study_input_error=bad_amount or bad_monthly,
        study_timeframes=study.TIMEFRAMES, study_series=study.SERIES,
        study_rows=rows, study_selected=selected, study_best=best,
        study_selected_rows=[(n, label, CSS[n], selected["series"].get(n))
                             for n, label in study.SERIES] if selected else (),
        study_chart=_chart(study, selected, scale) if selected else None,
        study_title=study.TITLE, study_needs=study.NEEDS,
        study_main=selected["series"][study.PRIMARY] if selected else None,
        study_how=study.how_it_works(data),
        study_first_day=data.days[0] if data.ready else None,
        study_last_day=data.days[-1] if data.ready else None,
    )
