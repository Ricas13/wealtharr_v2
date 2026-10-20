"""Phase 5: read-only, evidence-labelled quarterly 9Sig Breakdown.

Show saved historical advice, separately reproducible line arithmetic and
missing provenance without inventing a live evaluation. MarketObservation
does not retain raw feed unit or original source; it is NOT sufficient to
construct ApprovedPriceBundle and cannot authorize evaluate() trades.
No broker operations, price fetching, mutations or recalculated orders.
"""
import csv
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET

from .instrument_mapping import MAPPING_VERSION
from .market_signals import quarter_end_before
from .models import (
    InstanceOpeningSnapshot, MarketObservation, RebalanceExecution,
    StrategyInstance, StrategySignal, YahooSync,
)
from .strategies.nine_sig.v1 import (
    PARAMS, VERSION, _drawdown_from_spy, _quarterly_closes,
    quarter_number, signal_line_trace, signal_line_value,
)
from .v2_ledger_core import effective_entries, replay
from .v2_views import _render


ZERO = Decimal("0")
REQUIRED_HISTORY = 9
LABELS = {
    "growth": "LQQ3 growth (GBP)",
    "safe": "ERNS defensive (GBP)",
    "TQQQ": "TQQQ reference (USD)",
    "SPY": "SPY 30 Down reference (USD)",
}


def _quarter_end(q):
    year, index = divmod(q, 4)
    month = (index + 1) * 3
    return date(year, month, monthrange(year, month)[1])


def _owner_strategy(request, instance_id):
    return get_object_or_404(
        StrategyInstance.objects.select_related("account", "preset").filter(
            owner=request.user, account__owner=request.user
        ).exclude(account__kind="PAPER"), pk=instance_id,
    )


def _reviews(strategy):
    limit = quarter_end_before(timezone.localdate())
    return list(strategy.signals.filter(
        kind="QUARTERLY", as_of__lte=limit
    ).order_by("-as_of", "-pk"))


def _as_of(request, signals):
    raw = request.GET.get("quarter")
    if not raw:
        return signals[0] if signals else None
    if len(raw) != 10:
        raise Http404("Unknown review date")
    try:
        date.fromisoformat(raw)
    except ValueError:
        raise Http404("Invalid review date")
    chosen = next((x for x in signals if x.as_of.isoformat() == raw), None)
    if chosen is None:
        raise Http404("Review belongs to another account or is unavailable")
    return chosen


def _quality_rows(strategy, as_of):
    """Persisted observation audit, not claim that raw units were verified."""
    rows = []
    for key, symbol, currency in (
        ("growth", strategy.equity_symbol, "GBP"),
        ("safe", strategy.safe_symbol, "GBP"),
        ("TQQQ", "TQQQ", "USD"),
        ("SPY", "SPY", "USD"),
    ):
        observation = MarketObservation.objects.filter(
            symbol=symbol, as_of__lte=as_of,
            as_of__gte=as_of-timedelta(days=6),
        ).order_by("-as_of", "-pk").first()
        held = YahooSync.objects.filter(
            symbol=symbol, last_error__startswith="PRICE_HOLD:"
        ).exists()
        valid = bool(
            observation is not None and observation.currency == currency and
            observation.price > ZERO and not held
        )
        rows.append({
            "role": LABELS[key], "symbol": symbol,
            "observation": observation, "expected_currency": currency,
            "held": held, "date_ok": valid,
            "status": (
                "Price hold" if held else
                "No quarter-end close" if observation is None else
                "Wrong currency / invalid close" if not valid else
                "Stored date/currency only; raw unit unverified"
            ),
        })
    return rows


def _reference_row(symbol, q):
    end = _quarter_end(q)
    item = MarketObservation.objects.filter(
        symbol=symbol, as_of__lte=end,
        as_of__gte=end - timedelta(days=6),
    ).order_by("-as_of", "-pk").first()
    if item and item.price > ZERO and item.currency == "USD":
        return item
    return None


def _spy_reference(as_of):
    q = quarter_number(as_of)
    sequence = [_reference_row("SPY", x) for x in range(q-8, q+1)]
    if any(item is None for item in sequence):
        return (), None
    rows = tuple(sequence)
    closes = _quarterly_closes(((x.as_of, x.price) for x in rows), as_of)
    triggered, decline = _drawdown_from_spy(closes, q)
    peak = max(x.price for x in rows[:-1])
    return rows, {
        "peak": peak, "current": rows[-1].price, "decline_pct": (decline*100).quantize(Decimal("0.01")),
        "threshold": (peak * PARAMS.threshold_30_down).quantize(Decimal("0.0001")),
        "triggered": triggered,
    }


def _trace(strategy, as_of):
    """Reconstruct dated arithmetic only; never reissue proposal quantities."""
    if strategy.strategy_version != VERSION or strategy.mapping_version != MAPPING_VERSION:
        return None, "Pinned strategy or mapping version does not match the released version."
    if strategy.preset.slug != "nasdaq-quarterly-target":
        return None, "No released 9Sig calculation for this strategy."
    if (strategy.account.currency != "GBP" or
            strategy.equity_symbol != "LQQ3.L" or strategy.safe_symbol != "ERNS.L"):
        return None, "Unmapped or non-GBP strategy holdings."
    if strategy.ledger_revisions.filter(
        operation__in=("EDIT", "DELETE", "RESTORE")
    ).exists():
        return None, "Audited ledger corrections require a separately approved historical re-evaluation."
    opening = InstanceOpeningSnapshot.objects.filter(strategy=strategy).first()
    if opening and not hasattr(opening, "reconciliation"):
        return None, "The reported opening positions are not reconciled against original broker history."
    prior_resets = strategy.signals.filter(
        as_of__lt=as_of, reset_reason__gt=""
    ).exclude(review_outcome__in=("FALSE_POSITIVE", "NOT_APPLICABLE"))
    for reset_signal in prior_resets:
        if not RebalanceExecution.objects.filter(
                strategy=strategy, signal=reset_signal,
                baseline__isnull=False).exists():
            return None, (
                "A previous 60/40 reset was proposed but no owner-confirmed "
                "execution and pinned baseline were recorded."
            )
    events = [e for e in effective_entries(strategy) if e.occurred_on <= as_of]
    try:
        position = replay(events)
    except (ValidationError, ArithmeticError):
        return None, "Historical dated cash and holdings cannot be reconciled."
    if any(symbol not in (strategy.equity_symbol, strategy.safe_symbol)
           for symbol, qty in position["positions"].items() if qty > ZERO):
        return None, "An unmapped historical holding prevents a 9Sig audit."
    baselines = list(strategy.baselines.filter(
        as_of__lte=as_of
    ).order_by("-as_of", "-pk")[:1])
    if baselines:
        baseline = baselines[0]
        if not RebalanceExecution.objects.filter(
                strategy=strategy, signal=baseline.reset_signal,
                baseline=baseline).exists():
            return None, "Reset baseline lacks a linked owner-confirmed execution."
        if (baseline.strategy_version != VERSION or
                baseline.mapping_version != MAPPING_VERSION or
                not baseline.quote_evidence):
            return None, "Reset basis is missing pinned versions or original price evidence."
        anchor = baseline.as_of
        starting = baseline.equity_value
        label = "Owner-confirmed reset (recorded GBP equity basis)"
    else:
        first = next((e for e in events if e.kind == "BUY" and
                      e.symbol == strategy.equity_symbol), None)
        if first is None:
            return None, "An actual first growth purchase is not recorded."
        anchor = first.occurred_on
        starting = sum(
            (e.quantity * e.unit_price for e in events if
             e.kind == "BUY" and e.symbol == strategy.equity_symbol and
             e.occurred_on == anchor), ZERO
        )
        label = "First actual growth purchase (recorded execution cost)"
    if starting <= ZERO:
        return None, "The recorded starting growth basis is missing."
    flows = tuple(
        (e.occurred_on, e.amount if e.kind == "DEPOSIT" else -e.amount)
        for e in events if e.kind in ("DEPOSIT", "WITHDRAW")
        and anchor < e.occurred_on <= as_of
    )
    trace = signal_line_trace(
        start_date=anchor, as_of=as_of,
        starting_equity_cost=starting, contributions=flows,
    )
    line = signal_line_value(
        start_date=anchor, as_of=as_of,
        starting_equity_cost=starting, contributions=flows,
    )
    if line is None:
        return None, "The selected review predates the recorded starting basis."
    return {
        "anchor_date": anchor, "anchor_label": label,
        "starting": starting, "line": line, "cash": position["cash"],
        "positions": position["positions"],
        "flows": tuple({"date": d, "amount": a} for d, a in flows),
        "quarters": tuple({
            "quarter": "%s-Q%s" % (q//4, q%4+1),
            "opening": opening, "net_flows": contributed,
            "growth": opening * PARAMS.quarterly_growth,
            "closing": closing,
        } for q, opening, contributed, closing in trace),
    }, ""


def _growth_drawdown(strategy, as_of):
    """Reference only; never an input to 30 Down or executable advice.

    Raw quote provenance is absent in current persisted schema, so do not
    assert a trusted growth-series drawdown even if values are present.
    """
    count = MarketObservation.objects.filter(
        symbol=strategy.equity_symbol, as_of__lte=as_of
    ).count()
    return {
        "available": False,
        "observations": count,
        "reason": (
            "Information only, doesn't affect the advice. A verified running-high "
            "drawdown cannot be published until source/GBX-to-GBP unit evidence "
            "is stored for every growth observation."
        ),
    }


def _checklist(strategy, signal, trace, trace_error, quality, spy_rows, warning):
    evidence_issue = any(not x["date_ok"] for x in quality)
    holds = any(x["held"] for x in quality)
    checks = [
        ("Identity", bool(trace), trace_error or "Released strategy and historical line arithmetic available"),
        ("Account permission", not strategy.tracking_only,
         "Tracking only; broker buyability/quote units are unverified" if strategy.tracking_only
         else "Existing owner account enabled; this does not certify market price provenance"),
        ("Stored close dates", not evidence_issue,
         "One or more quarter-end dates/currencies are missing or held" if evidence_issue
         else "Stored dates/currencies align with basic requirements"),
        ("Raw quote units", False,
         "Historical MarketObservation records omit captured raw quote units and vendor identity"),
        ("SPY previous eight quarters", bool(spy_rows) and not holds,
         "Nine dated USD quarter observations" if spy_rows and not holds
         else "Incomplete/held SPY reference history"),
        ("Saved evaluation", bool(signal),
         "Saved immutable advisory event is displayed as recorded" if signal
         else "No recorded quarterly evaluation"),
    ]
    if warning:
        checks.append(("Ledger revisions", False, warning))
    return tuple({"title": n, "passed": ok, "detail": why} for n, ok, why in checks)


def _detail(strategy, signal):
    as_of = signal.as_of if signal else None
    if as_of is None:
        return {"signal": None, "quality": (), "trace": None, "trace_error": "",
                "spy": (), "spy_result": None, "checks": (), "growth": None,
                "saved_actions": (), "completed": False, "as_of": None}
    trace, trace_error = _trace(strategy, as_of)
    quality = _quality_rows(strategy, as_of)
    spy, spy_result = _spy_reference(as_of)
    if YahooSync.objects.filter(symbol="SPY", last_error__startswith="PRICE_HOLD:").exists():
        spy_result = None
    if any(x["held"] for x in quality):
        spy_result = None
    warn = ("Stored signals predate an edited ledger. No saved order may be "
            "certified as matching that revised history."
            if strategy.ledger_revisions.filter(
                operation__in=("EDIT", "DELETE", "RESTORE")
            ).exists() else "")
    return {
        "signal": signal, "as_of": as_of, "quality": quality,
        "trace": trace, "trace_error": trace_error,
        "spy": spy, "spy_result": spy_result, "growth": _growth_drawdown(strategy, as_of),
        "checks": _checklist(strategy, signal, trace, trace_error, quality, spy, warn),
        "saved_actions": tuple(x for x in signal.actions if
                               x.get("side") in ("BUY", "SELL", "HOLD")),
        "completed": RebalanceExecution.objects.filter(
            strategy=strategy, signal=signal).exists(),
        "caution": ("Stored proposals are not recomputed, validated against the current "
                    "broker, or executable from this explanation. No raw-unit-confirmed "
                    "ApprovedPriceBundle is persisted for these observations."),
    }


def _csv_safe(value):
    value = str(value if value is not None else "")
    if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def _export(strategy, detail):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        'attachment; filename="wealtharr-breakdown-%s-%s.csv"'
        % (strategy.pk, detail["as_of"].isoformat())
    )
    writer = csv.writer(response)
    writer.writerow(["kind", "field", "value", "evidence_status"])
    rows = [
        ("meta", "strategy_version", strategy.strategy_version, "pinned"),
        ("meta", "mapping_version", strategy.mapping_version, "pinned"),
        ("meta", "quarter_end", detail["as_of"].isoformat(), "saved review"),
        ("saved", "signal_key", detail["signal"].signal_key, "recorded only"),
        ("saved", "review_text", detail["signal"].message, "recorded only"),
    ]
    for key in ("line", "starting", "anchor_date", "cash"):
        if detail["trace"] is not None:
            rows.append(("arithmetic", key, detail["trace"][key], "reconstructed, not approved quote evaluation"))
    for x in detail["checks"]:
        rows.append(("check", x["title"], x["detail"], "passed" if x["passed"] else "not passed"))
    for line in detail["quality"]:
        obs = line["observation"]
        rows.extend([
            ("price", line["symbol"] + ":status", line["status"], "raw unit unverified"),
            ("price", line["symbol"] + ":observed_date", obs.as_of if obs else "", "persisted observation"),
            ("price", line["symbol"] + ":observed_price", obs.price if obs else "", "persisted observation"),
            ("price", line["symbol"] + ":observation_id", obs.pk if obs else "", "persisted observation"),
        ])
    for item in detail["trace"]["quarters"] if detail["trace"] is not None else ():
        rows.extend([
            ("quarter_trace", item["quarter"] + ":opening", item["opening"], "arithmetic"),
            ("quarter_trace", item["quarter"] + ":net_external_flow", item["net_flows"], "recorded ledger"),
            ("quarter_trace", item["quarter"] + ":closing", item["closing"], "arithmetic"),
        ])
    for action in detail["saved_actions"]:
        rows.append(("saved_action", action.get("side", ""), str(action), "saved historical advice only"))
    rows.append(("information_only", "growth_drawdown",
                 detail["growth"]["reason"], "no impact on advice"))
    for record in rows:
        writer.writerow([_csv_safe(v) for v in record])
    return response


@login_required
@require_GET
def breakdown_page(request, instance_id):
    strategy = _owner_strategy(request, instance_id)
    reviews = _reviews(strategy)
    selected = _as_of(request, reviews)
    detail = _detail(strategy, selected)
    if request.GET.get("export") == "csv" and selected is not None:
        return _export(strategy, detail)
    return _render(
        request, page_key="instance", page_title="Breakdown",
        instance=strategy, section="breakdown",
        breakdown_reviews=reviews, breakdown_detail=detail,
        breakdown_current=selected, breakdown_version=VERSION,
        breakdown_mapping=MAPPING_VERSION, breakdown_params=PARAMS,
    )
