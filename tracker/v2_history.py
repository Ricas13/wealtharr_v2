"""Performance (History) page for one strategy: the request layer over history_data.

GETs do not sync prices, recalculate trades, or write database records.
"""
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.views.decorators.http import require_GET

from .tenant_scope import get_strategy_or_404
from .v2_views import _render

ZERO = Decimal("0")

from .history_data import WINDOWS, _selected_window, build_history


def _chart(points):
    return tuple({
        "date": point.as_of.isoformat(), "value": str(point.value_gbp),
        "certified": point.certified,
    } for point in points)


def _owned(request, instance_id):
    return get_strategy_or_404(request, instance_id)


@login_required
@require_GET
def history_page(request, instance_id):
    strategy = _owned(request, instance_id)
    span = _selected_window(request)
    report = build_history(strategy, span=span)
    return _render(
        request, page_key="instance", page_title="Performance", section="history",
        instance=strategy, history=report, history_chart=_chart(report["points"]),
        history_ranges=WINDOWS, **_performance(request, strategy, report),
    )


def _performance(request, strategy, report=None):
    from . import v2_dashboard as dash
    points = dash.certified_points(strategy, report)
    flow_days = dash.external_cash_flow_days(strategy)
    monthly = dash.monthly_rows(points, cash_flow_days=flow_days)
    best, worst = dash.best_worst(monthly)
    returns = dash.period_returns(points, cash_flow_days=flow_days)
    since = next((r for r in returns if r["label"] == "Since start"), None)
    # USD QQQ ETF price change (a Nasdaq-100 proxy) is NOT like-for-like with a
    # cash-flow-adjusted portfolio in its account currency. Show it separately,
    # without fabricating 'outperformance' in percentage points.
    nasdaq_usd = since["indexes"][0] if since and since["indexes"] else None
    last = points[-1] if points else None
    return {
        "perf_points": bool(points),
        "perf_chart": dash.chart(points, span=dash.pick_range(request),
                                 mode=dash.pick_mode(request, "pct"), cash_flow_days=flow_days),
        "perf_ranges": dash.RANGES, "perf_indexes": dash.INDEXES,
        "perf_monthly": monthly, "perf_best": best, "perf_worst": worst,
        "perf_drawdown": dash.drawdown(points, cash_flow_days=flow_days),
        "perf_money_in": dash.money_in_by_month([strategy]),
        "perf_returns": returns,
        "perf_index_rows": tuple({"name": name, "values": [r["indexes"][i] for r in returns]}
                                 for i, (_, name) in enumerate(dash.INDEXES)),
        "perf_gain_pct": ((last[1] - last[2]) / last[2] * 100).quantize(Decimal("0.1"))
        if last and last[2] > ZERO else None,
        "perf_xirr": dash.xirr_pct(strategy, points),
        "perf_nasdaq_usd": nasdaq_usd,
        "perf_days": (timezone.localdate() - points[0][0]).days if points else None,
    }
