"""The signed-in Overview page: portfolio rows, next actions and combined figures."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.views.decorators.http import require_GET

from .history_data import build_history
from .models import MarketObservation, StrategyInstance, YahooSync
from .v2_views import _render
from .strategies.instruments import symbol_labels, symbols as fund_symbols
from .strategies.registry import current_review_range

ZERO = Decimal("0")


def _next_quarter(today):
    from .signal_status import _next_end
    return _next_end(today)


@login_required
@require_GET
def overview_page(request):
    items = list(StrategyInstance.objects.filter(
        owner=request.user, account__owner=request.user
    ).exclude(account__kind="PAPER").select_related("account", "preset")
                .order_by("created_at", "pk"))
    rows = []
    contributed = ZERO
    complete_funding = True
    total = ZERO
    included_funding = ZERO
    included_funding_known = True
    included_dates = set()
    excluded = []
    today = timezone.localdate()
    # Combined figures add up one currency only (no guessed exchange rates): the
    # first strategy's. Strategies in another currency are listed, not combined.
    from .money import symbol_for
    home = items[0].account.currency if items else "GBP"
    other_currency = [i.name for i in items if i.account.currency != home]
    # A strategy counts when it has a certified value from the last 8 days
    # (latest closes, so weekends and holidays don't blank the total).
    fresh_after = today - timedelta(days=8)
    for instance in items:
        report = build_history(instance)
        combined = instance.account.currency == home
        if not combined:
            pass
        elif report["actual_net_funding"] is None:
            complete_funding = False
        else:
            contributed += report["actual_net_funding"]
        latest = report["latest_certified"]
        if not combined:
            pass
        elif latest and latest.as_of >= fresh_after:
            total += latest.value_gbp
            included_dates.add(latest.as_of)
            if report["actual_net_funding"] is None:
                included_funding_known = False
            else:
                included_funding += report["actual_net_funding"]
        else:
            excluded.append(instance.name)
        rows.append({
            "strategy": instance, "status": report["status"],
            "reason": report["reason"], "value": latest.value_gbp if latest else None,
            "as_of": latest.as_of if latest else None,
            "contributed": report["actual_net_funding"],
            "last_recorded": report["last_point"],
            "cur": symbol_for(instance.account.currency), "combined": combined,
        })
    valued = len(items) - len(excluded) - len(other_currency)
    aggregate = total if valued else None
    gained = total - included_funding if valued and included_funding_known else None
    isa_total = sum((row["contributed"] for row in rows
                     if row["strategy"].account.kind == "ISA" and
                     row["contributed"] is not None), ZERO)
    return _render(
        request, page_key="overview", page_title="Overview",
        **_overview_extras(request, items, rows, today),
        overview_rows=rows, overview_total=aggregate, overview_other_currency=other_currency,
        page_currency=home, overview_reporting_currency=home,
        overview_contributed=contributed if complete_funding and items else None,
        overview_gain=gained,
        overview_same_date=bool(items) and not excluded and not other_currency and len(included_dates) == 1,
        overview_mixed_dates=len(included_dates) > 1,
        overview_valuation_dates=tuple(sorted(included_dates)),
        overview_excluded=excluded,
        overview_isa_contributed=isa_total, overview_has_isa=any(
            row["strategy"].account.kind == "ISA" for row in rows
        ), overview_review_date=_next_quarter(today),
    )


def _open_action(strategy, summary, today):
    """One short to-do for a strategy, from saved reviews and the ledger."""
    from .cockpit_execution import proposed_actions
    from .models import RebalanceExecution
    from .v2_go_live import setup_missing
    if strategy.tracking_only:
        return {"kind": "golive", "title": "Not live yet", "detail": "Check the ledger, then go live"}
    if setup_missing(strategy):
        return {"kind": "golive", "title": "Setup not finished", "detail": "Confirm the instruments to start recording"}
    from .signal_status import _last_action, changed_since_review, _price_status
    manual = _last_action(strategy)
    if manual and manual.kind in ("SPIKE_WATCH", "THIRTY_DOWN"):
        return {"kind": "alert", "title": "Manual market review",
                "detail": manual.title or "Open Today to review the market warning"}
    if not summary["positions"] and summary["cash"] > ZERO:
        return {"kind": "start", "title": "Buy your opening position",
                "detail": "Buy what the strategy holds today"}
    signal = strategy.signals.filter(
        kind="QUARTERLY", as_of__range=current_review_range(strategy, today),
    ).exclude(review_outcome__in=("FALSE_POSITIVE", "NOT_APPLICABLE")).order_by("-as_of", "-pk").first()
    if signal and proposed_actions(signal) and not RebalanceExecution.objects.filter(
            signal=signal).exists():
        from .models import SignalFill
        if SignalFill.objects.filter(strategy=strategy, signal=signal).exists():
            return {"kind": "pending", "title": "Reconcile partial fills",
                    "detail": "Original orders are no longer current. Open Today to finish recording actual fills."}
        if changed_since_review(strategy, signal):
            return {"kind": "data", "title": "Review needs recalculation",
                    "detail": "The ledger changed after the review; do not use the old quantities."}
        from .money import symbol_for
        trades = " · ".join("%s ≈ %s%s %s" % (
            a["side"].title(), symbol_for(strategy.account.currency), "{:,.0f}".format(Decimal(str(a.get("estimated_gbp") or 0))), a["symbol"])
            for a in proposed_actions(signal))
        from .strategies.registry import module_for
        plugin = module_for(strategy)
        word = plugin.cadence_word if plugin else "quarterly"
        title = ("%d rebalance" % signal.as_of.year if word == "yearly" else
                 "%s rebalance" % signal.as_of.strftime("%B") if word == "monthly" else
                 "Q%d rebalance" % ((signal.as_of.month - 1) // 3 + 1))
        return {"kind": "review", "title": title, "detail": trades,
                "signal": signal}
    # Do not leave a verified new deposit hidden behind Overview\u2019s "Nothing due".
    from .contribution_guidance import new_money_guidance
    fresh, _, _ = _price_status(strategy, summary)
    waiting = new_money_guidance(strategy, summary, prices_ready=fresh, today=today)
    if waiting:
        from .money import symbol_for
        return {"kind": "new_money", "title": "Invest new deposit",
                "detail": "%s%s of recorded cash awaits a strategy-specific purchase" % (
                    symbol_for(strategy.account.currency), waiting["amount"])}
    return None


def _overview_extras(request, items, rows, today):
    from . import v2_dashboard as dash
    from .models import NotificationPreference
    from .services import ledger_summary
    from .v2_price_health import strategy_price_health
    span = dash.pick_range(request)
    mode = dash.pick_mode(request)
    home = items[0].account.currency if items else "GBP"
    live = [s for s in items if not s.tracking_only and s.account.currency == home]
    points, chart_omitted = dash.combine_with_omitted(live)
    combined_flow_days = frozenset(day for s in live for day in dash.external_cash_flow_days(s))
    missing_chart_evidence = bool(live) and any(not dash.certified_points(s) for s in live)
    todos = []
    assets = {}
    assets_omitted = []
    for row in rows:
        strategy = row["strategy"]
        try:
            summary = ledger_summary(strategy)
        except (ValidationError, ArithmeticError):
            summary = None
        own = dash.recorded_points(strategy)
        row["gain"] = (row["value"] - row["contributed"]
                       if row["value"] is not None and row["contributed"] is not None else None)
        row["xirr"] = dash.xirr_pct(strategy, own) if not strategy.tracking_only else None
        health = strategy_price_health(strategy, summary)
        row["price_health"] = health
        row["action"] = _open_action(strategy, summary, today) if summary else None
        # A stored quarterly signal is not permission to display a trade as
        # actionable when today's complete price evidence is missing.
        if row["action"] and row["action"]["kind"] == "review" and not health["ready"]:
            row["action"] = {
                "kind": "data", "title": "Review blocked by data",
                "detail": health["reason"] or "The required closing prices are not verified.",
            }
        if row["action"]:
            todos.append({"strategy": strategy, **row["action"]})
        if (summary and summary["value"] is not None and not strategy.tracking_only
                and strategy.account.currency == home and not health["ready"]):
            assets_omitted.append(strategy.name)  # stale or held prices: no allocation shown
        elif (summary and summary["value"] is not None and not strategy.tracking_only
                and strategy.account.currency == home):
            for symbol, qty in summary["positions"].items():
                mark = summary["marks"].get(symbol)
                if mark and qty > ZERO:
                    label = symbol_labels(strategy).get(symbol, symbol)
                    assets[label] = assets.get(label, ZERO) + qty * mark.price
            assets["Cash"] = assets.get("Cash", ZERO) + summary["cash"]
    asset_total = sum(assets.values(), ZERO)
    asset_rows = tuple({
        "label": label, "value": value,
        "pct": (value / asset_total * 100).quantize(Decimal("0.1")) if asset_total > ZERO else None,
        "css": {"Growth": "w2-c1", "Safe": "w2-c2", "Cash": "w2-c4"}.get(label, "w2-c3"),
        "width": float(value / asset_total * 100) if asset_total > ZERO else 0,
    } for label, value in assets.items()) if asset_total > ZERO else ()
    valued = [r for r in rows if r["value"] is not None and r["combined"]]
    total = sum((r["value"] for r in valued), ZERO)
    for r in rows:
        r["share"] = (float(r["value"] / total * 100)
                      if r["value"] is not None and r["combined"] and total > ZERO else 0)
    next_end = _next_quarter(today)
    coming = []
    for todo in todos:
        if todo["kind"] == "review":
            coming.append({"date": None, "strategy": todo["strategy"].name,
                           "event": todo["title"], "todo": todo["detail"]})
    pref = NotificationPreference.objects.filter(owner=request.user).first()
    if items and (pref is None or pref.monthly_reminders):
        first = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        coming.append({"date": first, "strategy": "All", "event": "Monthly deposit reminder",
                       "todo": "Record your top-up if you made one"})
    if live:
        coming.append({"date": next_end, "strategy": ", ".join(s.name for s in live),
                       "event": "Quarter end", "todo": "Review arrives after the close"})
    isa = dash.isa_allowance(items)
    if isa:
        coming.append({"date": isa["resets"], "strategy": "All ISAs", "event": "New ISA tax year",
                       "todo": "Allowance resets"})
    coming.sort(key=lambda c: (c["date"] is not None, c["date"] or today))
    symbols = sorted({x for s in live for x in fund_symbols(s)})
    prices = [MarketObservation.objects.filter(symbol=x, currency=home).order_by("-as_of").first()
              for x in symbols]
    last_sync = YahooSync.objects.filter(symbol__in=symbols).order_by("-last_attempt").first()
    change, change_pct = dash.day_change(points, cash_flow_days=combined_flow_days)
    combined_chart = dash.chart(points, span=span, mode=mode, indexes=False,
                                cash_flow_days=combined_flow_days)
    if missing_chart_evidence:
        combined_chart["reason"] = (
            "A combined chart needs certified valuations for every live strategy "
            "in the reporting currency. No partial total is plotted.")
    elif live and not points:
        combined_chart["reason"] = (
            "No common certified valuation dates exist for every live strategy "
            "in the reporting currency. No partial total is plotted.")
    return {
        "ov_chart": combined_chart,
        "ov_chart_omitted": chart_omitted,
        "ov_assets_omitted": assets_omitted,
        "ov_ranges": dash.RANGES, "ov_todos": tuple(todos), "ov_assets": asset_rows,
        "ov_coming": tuple(coming), "ov_isa": isa,
        "ov_prices": tuple(p for p in prices if p), "ov_last_sync": last_sync,
        "ov_alerts": dash.alerts_summary(request.user),
        "ov_day_change": change, "ov_day_pct": change_pct,
        "ov_xirr": dash.xirr_pct(live[0], points) if len(live) == 1 else None,
        "ov_today": today,
    }
