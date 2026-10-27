"""Phase 4: source-grounded Today page for one owner-owned strategy.

No evaluation, HTTP price request, broker link or trade is made by GET. This
adapter only presents persisted reviews, actual ledger balances and independently
verified quote-date valuation evidence. Existing 9Sig rules are not modified.
"""
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET

from .market_data import QUOTE_MAX_AGE
from .signal_status import _last_action, _price_status, changed_since_review
from .cockpit_execution import completion_rows, proposed_actions
from .core.presentation import account_value, holding_rows
from .models import RebalanceExecution, SignalFill, StrategyInstance
from .services import ledger_summary
from .strategies.instruments import symbol_labels, symbols
from .strategies.registry import last_review_before, module_for, next_review
from .v2_views import _render
from .v2_go_live import setup_missing


ZERO = Decimal("0")


def _owned(request, instance_id):
    return get_object_or_404(
        StrategyInstance.objects.select_related("account", "preset").filter(
            owner=request.user, account__owner=request.user
        ).exclude(account__kind="PAPER"), pk=instance_id,
    )


def _readiness(strategy, report):
    if strategy.tracking_only:
        return False, (
            "This strategy isn't live yet. Confirm the instruments in your "
            "account to switch on live prices and the rules output."
        ), ()
    fresh, why, marks = _price_status(strategy, report)
    if not fresh:
        return False, why, marks
    # A viable price is not proof a quarterly review exists. We refuse to
    # silently reinterpret old persisted signals with revised rule versions.
    if strategy.strategy_version or strategy.mapping_version:
        from .strategies.registry import describe_strategy
        from .instrument_mapping import MAPPING_VERSION
        desc = describe_strategy(strategy.preset.slug)
        if (desc is None or not desc.accepts(strategy.strategy_version)
                or strategy.mapping_version != MAPPING_VERSION):
            return False, "The pinned strategy or mapping version has changed.", marks
    return True, "", marks


def _opening_buys(strategy, cash):
    """Target-weight split of uninvested cash for a strategy that holds nothing yet.

    Strategies without fixed weights supply today's split through their plug-in;
    when it can't be computed there is no opening card rather than a guess.
    """
    from .core.catalogue import rows_for
    from .models import MarketObservation, YahooSync
    from .strategies.instruments import role_symbols
    from .strategies.registry import module_for
    lines = rows_for(strategy.strategy_key or strategy.preset.slug,
                     strategy.account.broker, strategy.account.kind)
    # The instance's own pinned funds, not whatever the catalogue lists today.
    pinned = role_symbols(strategy)
    symbol = {line.role: pinned.get(line.role, line.ticker) for line in lines}
    plugin = module_for(strategy)
    weights = {symbol[line.role]: line.weight for line in lines}
    if plugin and plugin.current_weights:
        weights = plugin.current_weights(strategy)
        if weights is None:
            return ()
    fresh_after = timezone.localdate() - QUOTE_MAX_AGE
    rows = []
    for line in lines:
        ticker = symbol[line.role]
        weight = weights.get(ticker, Decimal("0"))
        if not weight:
            continue
        amount = (cash * weight).quantize(Decimal("0.01"))
        held = YahooSync.objects.filter(symbol=ticker, last_error__startswith="PRICE_HOLD").exists()
        quote = None if held else MarketObservation.objects.filter(
            symbol=ticker, currency=strategy.account.currency, as_of__gte=fresh_after,
        ).order_by("-as_of").first()
        rows.append({
            "symbol": ticker, "role": line.role_label, "pct": weight * 100,
            "amount": amount, "price": quote.price if quote else None,
            "shares": (amount / quote.price).quantize(Decimal("0.01")) if quote else None,
        })
    return tuple(rows)


def _opening_allocation_available(strategy, plugin):
    """Don't instruct a first purchase while a cash allocation is intentional.

    A missing dynamic weight signal must not turn into a guessed opening buy.
    """
    if not plugin or not plugin.allows_cash_only:
        return True
    if plugin.current_weights is None:
        return False
    weights = plugin.current_weights(strategy)
    return weights is not None and any(w > ZERO for w in weights.values())


def _signal_rows(strategy):
    signals = strategy.signals.filter(
        kind="QUARTERLY"
    ).order_by("-as_of", "-pk")[:8]
    executed = set(
        RebalanceExecution.objects.filter(
            strategy=strategy, signal__in=signals
        ).values_list("signal_id", flat=True)
    )
    linked = {}
    for signal_id in SignalFill.objects.filter(
        strategy=strategy, signal__in=signals
    ).values_list("signal_id", flat=True):
        linked[signal_id] = linked.get(signal_id, 0) + 1
    return tuple({
        "signal": s,
        "complete": s.pk in executed,
        "partial_count": linked.get(s.pk, 0),
        "has_trades": bool(proposed_actions(s)),
    } for s in signals)


def _snapshot_rows(strategy):
    # The hourly writer stores only quote-day, GBP-evidenced samples.
    # Values are labelled historical snapshots, never tick-by-tick prices.
    return tuple(strategy.value_snapshots.order_by("-as_of")[:12])


@login_required
@require_GET
def today_page(request, instance_id):
    strategy = _owned(request, instance_id)
    plugin = module_for(strategy)
    report = ledger_summary(strategy)
    ready, reason, marks = _readiness(strategy, report)
    notice = _last_action(strategy)
    progress = ()
    partial = False
    executed = False
    if notice and notice.kind == "QUARTERLY":
        executed = RebalanceExecution.objects.filter(
            signal=notice, strategy=strategy
        ).exists()
        try:
            fills = list(SignalFill.objects.filter(
                strategy=strategy, signal=notice
            ).select_related("ledger_entry").order_by("pk"))
            progress = completion_rows(notice, fills)
            actions = proposed_actions(notice)
            progress = tuple(dict(row, gbp=actions[row["index"]].get("estimated_gbp"))
                             for row in progress)
            partial = bool(fills)
        except ValidationError:
            reason = "Recorded fills need audited reconciliation."
            ready = False
    elif notice and notice.kind not in ("THIRTY_DOWN", "SPIKE_WATCH"):
        notice = None
    if (notice and notice.kind == "QUARTERLY" and not partial
            and changed_since_review(strategy, notice)):
        ready = False
        reason = (
            "The ledger changed after this review. An updated, verified "
            "evaluation is needed before relying on its quantities."
        )
    if (notice and notice.kind == "QUARTERLY" and partial and
            not strategy.tracking_only):
        status = "record"
        headline = "Finish recording actual fills"
        explanation = (
            "A real trade is already recorded for this quarter. Record the rest; "
            "the review then finishes by itself after the next closing prices"
            + (" (a reset review needs you to press Finish)." if notice.reset_reason else ".")
            + " This is not a new rules output."
        )
    elif notice and notice.kind in ("THIRTY_DOWN", "SPIKE_WATCH"):
        status = "review"
        headline = notice.title
        explanation = notice.message
    elif (not strategy.tracking_only and not notice and not report["positions"]
          and report["cash"] > ZERO and _opening_allocation_available(strategy, plugin)):
        status = "start"
        headline = "Opening position from the rules"
        explanation = (
            "The rules split your cash as below. If you go ahead, place the buys yourself in "
            f"{strategy.account.broker_label}, "
            "then record what you actually bought.")
    elif not strategy.tracking_only and setup_missing(strategy):
        status = "setup"
        headline = "Finish setting up"
        explanation = ("This strategy is missing its start date or pinned instruments, "
                       "so nothing can be recorded yet. It takes one confirmation.")
    elif (not strategy.tracking_only and not notice and not report["positions"]
          and report["cash"] <= ZERO):
        status = "empty"
        headline = "Add your first deposit"
        explanation = ("Record the money you've put into your "
                       f"{strategy.account.kind} with Add money. You'll then see "
                       "the rules output for it.")
    elif not ready:
        status = "blocked"
        headline = ("Not live yet" if strategy.tracking_only
                    else "Cannot compute a reliable rules output")
        explanation = reason
    elif ready and notice and notice.kind == "QUARTERLY" and progress and not executed:
        status = "trade"
        headline = "%s rules output" % (plugin.cadence_word if plugin else "quarterly").capitalize()
        explanation = (
            "The rules calculated these amounts from your records and verified closes. "
            "Check them against your broker and decide yourself. "
            "Nothing is executed by Wealtharr. Record only fills you actually made."
        )
    else:
        status = "idle"
        headline = "Nothing to do right now"
        explanation = "No outstanding %s rules output." % (
            plugin.cadence_word if plugin else "quarterly")
    # New confirmed cash can be invested between reviews without forcing an
    # early periodic rebalance. Never compete with an existing trade, a data
    # hold, a tracking-only strategy, or the Nasdaq daily evaluator.
    from .contribution_guidance import new_money_guidance
    new_money = (new_money_guidance(strategy, report, prices_ready=ready)
                 if status == "idle" else None)
    if new_money:
        status = "new_money"
        headline = "Rules output for your new deposit"
        explanation = new_money["note"]
    next_end = next_review(strategy, timezone.localdate())
    previous_end = last_review_before(strategy, timezone.localdate())
    span = (next_end - previous_end).days
    review_progress = (100 * (timezone.localdate() - previous_end).days / span) if span > 0 else None
    price_date = min((m.as_of for m in marks if m is not None), default=None) if ready else None
    display_value = account_value(
        net_cash=report["cash"], portfolio_value=report["value"],
        prices_verified=ready, has_holdings=bool(report["positions"]),
    )
    gain = (
        report["profit"] if ready and report["value"] is not None
        else None
    )
    symbol_roles = symbol_labels(strategy)
    from .v2_price_health import strategy_price_health
    health = strategy_price_health(strategy, report)
    return _render(
        request, page_key="instance", page_title="Dashboard",
        instance=strategy, section="today",
        **_dashboard(request, strategy, report, ready, symbol_roles, next_end),
        head_prices=health, today_price_health=health,
        today_new_money=new_money,
        today_status=status, today_headline=headline,
        today_explanation=explanation, today_blocking_reason=reason,
        today_ready=ready, today_signal=notice, today_partial=partial, today_executed=executed,
        today_progress=progress if (status in ("trade", "record")) else (),
        today_opening=_opening_buys(strategy, report["cash"]) if status == "start" else (),
        today_value=display_value, today_gain=gain,
        today_cash=report["cash"], today_contributed=report["net_contributed"],
        today_quote_date=price_date, today_next_review=next_end,
        today_days_to_review=(next_end-timezone.localdate()).days,
        today_review_progress=review_progress,
        today_holdings=holding_rows(report, roles=symbol_roles, show_quotes=ready),
        today_snapshots=_snapshot_rows(strategy),
        today_quarters=_signal_rows(strategy),
        today_chart_caution=(
            "Only persisted GBP-evidenced valuation dates are listed. "
            "A return rate is withheld until audited money-flow metrics are available."
        ),
    )


def _dashboard(request, strategy, report, ready, roles, next_end):
    """Cards for the compact dashboard; all read-only and owner-scoped."""
    from . import v2_dashboard as dash
    from .core.catalogue import MappingNotAvailable, rows_for
    try:
        lines = rows_for(strategy.strategy_key or strategy.preset.slug,
                         strategy.account.broker, strategy.account.kind)
    except MappingNotAvailable:
        lines = ()
    plugin = module_for(strategy)
    points = dash.certified_points(strategy)
    span = dash.pick_range(request)
    mode = dash.pick_mode(request)
    flow_days = dash.external_cash_flow_days(strategy)
    change, change_pct = dash.day_change(points, cash_flow_days=flow_days) if ready else (None, None)
    returns = dash.period_returns(points, cash_flow_days=flow_days)
    return {
        "dash_points": bool(points),
        "dash_chart": dash.chart(points, span=span, mode=mode, cash_flow_days=flow_days),
        "dash_ranges": dash.RANGES, "dash_indexes": dash.INDEXES,
        "dash_day_change": change, "dash_day_pct": change_pct,
        "dash_gain_pct": (
            (report["profit"] / report["net_contributed"] * 100).quantize(Decimal("0.1"))
            if ready and report["value"] is not None and report["net_contributed"] > ZERO
            else None),
        "dash_xirr": dash.xirr_pct(strategy, points) if ready else None,
        "dash_allocation": dash.allocation(
            report, lines, roles,
            targets=plugin.current_weights(strategy) if plugin and plugin.current_weights and ready else None,
        ) if ready else None,
        "dash_allocation_label": plugin.allocation_label if plugin else "Target",
        "dash_plugin_template": plugin.dashboard_template if plugin else "",
        **(plugin.dashboard_context(strategy, report, next_end)
           if plugin and plugin.dashboard_context and ready else {}),
        "dash_holdings": dash.holdings_detail(strategy, report, roles) if ready else (),
        "dash_returns": tuple(r for r in returns if r["label"] in (
            "1 week", "1 month", "3 months", "Since start")),
        "dash_recent": dash.recent_entries(strategy),
        "dash_isa": dash.isa_allowance([strategy]),
        "dash_alerts": dash.alerts_summary(request.user, strategy),
        "dash_symbols": symbols(strategy),
        "dash_today": timezone.localdate(),
    }
