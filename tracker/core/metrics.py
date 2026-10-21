"""Strategy-independent, auditable GBP performance metrics (Section D).

Pure Decimal functions: no Django, IO, market feed, broker, or strategy rules.
An exact return path needs independently verified valuation endpoints AND a
same-price pre/post valuation for EACH dated external cash flow. A daily NAV
series alone cannot certify returns across deposits/withdrawals.

Amounts are GBP. External flows are POSITIVE for deposits and NEGATIVE for
withdrawals; purchases, sales, dividends and fees are INTERNAL investment
events and must appear only in valuations (never again as external flows).
Values are never rounded while chain-linking or unitising.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from typing import Mapping

from tracker.performance import xirr, annualized_return

D = Decimal
ZERO = D("0")
ONE = D("1")
PERCENT = D("100")
METRICS_VERSION = "universal.metrics.v1.0.0"


@dataclass(frozen=True)
class VerifiedValuation:
    as_of: date
    value_gbp: Decimal
    observation_ids: tuple[str, ...]
    verified: bool
    currency: str = "GBP"


@dataclass(frozen=True)
class ExternalFlow:
    event_id: str
    occurred_on: date
    amount_gbp: Decimal
    sequence: int = 0  # stable within-day source ordering, NOT an assumed timestamp


@dataclass(frozen=True)
class FlowBoundary:
    flow: ExternalFlow
    before: VerifiedValuation
    after: VerifiedValuation


@dataclass(frozen=True)
class PortfolioPeriod:
    opening: VerifiedValuation  # NAV AFTER initial funding
    closing: VerifiedValuation  # NAV AFTER any closing-day external flows
    flows: tuple[ExternalFlow, ...] = ()
    boundaries: tuple[FlowBoundary, ...] = ()
    observations: tuple[VerifiedValuation, ...] = ()  # optional intervening daily closes


@dataclass(frozen=True)
class MetricAudit:
    stage: str
    as_of: date
    portfolio_gbp: Decimal
    net_external_flow_gbp: Decimal
    unit_nav: Decimal | None
    observation_ids: tuple[str, ...]
    units_outstanding: Decimal | None = None
    units_issued: Decimal = ZERO


@dataclass(frozen=True)
class PeriodMetrics:
    status: str  # ok, partial, cannot_compute
    reason_code: str
    opening_gbp: Decimal | None
    closing_gbp: Decimal | None
    net_external_flows_gbp: Decimal | None
    cash_gain_gbp: Decimal | None
    xirr_annual: Decimal | None
    cumulative_return: Decimal | None  # exact TWR; never an XIRR-derived proxy
    annualized_twr: Decimal | None
    max_drawdown: Decimal | None
    audit: tuple[MetricAudit, ...] = ()

    def audit_rows(self) -> tuple[dict[str, str], ...]:
        """Stable, CSV/JSON-ready valuation and unit issuance evidence."""
        return tuple({
            "stage": point.stage, "as_of": point.as_of.isoformat(),
            "portfolio_gbp": str(point.portfolio_gbp),
            "net_external_flow_gbp": str(point.net_external_flow_gbp),
            "unit_nav": "" if point.unit_nav is None else str(point.unit_nav),
            "observation_ids": "|".join(point.observation_ids),
            "units_outstanding": ("" if point.units_outstanding is None
                                  else str(point.units_outstanding)),
            "units_issued": str(point.units_issued),
        } for point in self.audit)

    def summary_row(self) -> dict[str, str]:
        """Missing metrics are empty rather than zero or fabricated returns."""
        keys = ("opening_gbp", "closing_gbp", "net_external_flows_gbp",
                "cash_gain_gbp", "xirr_annual", "cumulative_return",
                "annualized_twr", "max_drawdown")
        return {
            "metrics_version": METRICS_VERSION,
            "status": self.status, "reason_code": self.reason_code,
            **{key: "" if getattr(self, key) is None
               else str(getattr(self, key)) for key in keys},
        }


@dataclass(frozen=True)
class InceptionMetrics:
    status: str
    reason_code: str
    net_contributed_gbp: Decimal | None
    cash_gain_gbp: Decimal | None
    xirr_annual: Decimal | None
    simple_cumulative_return: Decimal | None  # only a SINGLE funding deposit
    cash_flows: tuple[ExternalFlow, ...] = ()
    # Descriptive ratio; NOT a time- or money-weighted investment return.
    gain_vs_net_contributions_pct: Decimal | None = None

    def audit_rows(self) -> tuple[dict[str, str], ...]:
        return tuple({
            "event_id": flow.event_id,
            "occurred_on": flow.occurred_on.isoformat(),
            "signed_external_flow_gbp": str(flow.amount_gbp),
            "source_sequence": str(flow.sequence),
        } for flow in self.cash_flows)


@dataclass(frozen=True)
class SleeveDrift:
    role: str
    actual_weight: Decimal
    target_weight: Decimal
    drift_percentage_points: Decimal


def _decimal(value):
    return isinstance(value, Decimal) and value.is_finite()


def _approved(point):
    return (isinstance(point, VerifiedValuation) and
            type(point.as_of) is date and _decimal(point.value_gbp) and
            point.value_gbp >= ZERO and point.currency in ("GBP", "USD", "EUR") and
            point.verified is True and
            isinstance(point.observation_ids, tuple) and
            bool(point.observation_ids) and
            all(isinstance(s, str) and bool(s.strip()) for s in point.observation_ids) and
            len(set(point.observation_ids)) == len(point.observation_ids))


def _valid_flows(flows, start, end, *, include_start=False):
    if not isinstance(flows, (tuple, list)):
        return False
    keys = set()
    identifiers = set()
    for flow in flows:
        if (not isinstance(flow, ExternalFlow) or
                not isinstance(flow.event_id, str) or not flow.event_id.strip() or
                type(flow.occurred_on) is not date or
                type(flow.sequence) is not int or flow.sequence < 0 or
                not _decimal(flow.amount_gbp) or flow.amount_gbp == ZERO or
                not (start <= flow.occurred_on if include_start else start < flow.occurred_on) or
                flow.occurred_on > end or
                (flow.occurred_on, flow.sequence) in keys or
                flow.event_id in identifiers):
            return False
        keys.add((flow.occurred_on, flow.sequence))
        identifiers.add(flow.event_id)
    return True


def _result(reason, opening=None, closing=None, flow_sum=None, gain=None, rate=None):
    return PeriodMetrics(
        "cannot_compute" if opening is None or closing is None else "partial",
        reason, opening, closing, flow_sum, gain, rate, None, None, None)


def _drawdown_from_nav(nav_values):
    peak = ZERO
    worst = ZERO
    for value in nav_values:
        if not _decimal(value) or value < ZERO:
            return None
        peak = max(peak, value)
        if peak <= ZERO:
            return None
        worst = max(worst, ONE - value / peak)
    return worst


def _exact_unit_nav(period: PortfolioPeriod, flows):
    """Return (ending unit NAV, audit path, error); no hidden flow netting.

    A before->after flow must use identical quote evidence so that change is
    ONLY the flow. No same-date daily observation is accepted: daily data do
    not identify its position relative to an intraday funding event.
    """
    if not isinstance(period.boundaries, (list, tuple)) or not isinstance(
            period.observations, (list, tuple)):
        return None, (), "INVALID_RETURN_OBSERVATIONS"
    if len(period.boundaries) != len(flows):
        return None, (), "MISSING_FLOW_BOUNDARIES"
    if any(not isinstance(b, FlowBoundary) or
           not isinstance(b.flow, ExternalFlow) for b in period.boundaries):
        return None, (), "FLOW_BOUNDARY_MISMATCH"
    by_id = {b.flow.event_id: b for b in period.boundaries}
    if len(by_id) != len(flows) or set(by_id) != {f.event_id for f in flows}:
        return None, (), "FLOW_BOUNDARY_MISMATCH"

    if any(not _approved(p) or p.currency != period.opening.currency or
           p.as_of <= period.opening.as_of or
           p.as_of >= period.closing.as_of for p in period.observations):
        return None, (), "UNVERIFIED_INTERMEDIATE_VALUATION"
    dates = [p.as_of for p in period.observations]
    if len(set(dates)) != len(dates) or any(d in {f.occurred_on for f in flows} for d in dates):
        return None, (), "AMBIGUOUS_INTERMEDIATE_ORDER"

    events = [(p.as_of, -1, "daily", p) for p in period.observations]
    for flow in flows:
        boundary = by_id[flow.event_id]
        if boundary.flow != flow:
            return None, (), "FLOW_BOUNDARY_MISMATCH"
        before, after = boundary.before, boundary.after
        if (not _approved(before) or not _approved(after) or
                before.currency != period.opening.currency or
                after.currency != period.opening.currency or
                before.as_of != flow.occurred_on or after.as_of != flow.occurred_on or
                before.observation_ids != after.observation_ids or
                before.value_gbp <= ZERO or after.value_gbp <= ZERO or
                after.value_gbp - before.value_gbp != flow.amount_gbp):
            return None, (), "FLOW_BOUNDARY_NOT_EXACT"
        events.append((flow.occurred_on, flow.sequence, "flow", boundary))
    events.sort(key=lambda event: (event[0], event[1]))
    # Closing-day flow order is only provable when the end snapshot uses the
    # very same price evidence as the final post-flow valuation. Otherwise
    # date-only records cannot establish that the closing price came later.
    closing_day_flows = [b for _, _, kind, b in events
                         if kind == "flow" and b.flow.occurred_on == period.closing.as_of]
    if closing_day_flows and (
            period.closing.observation_ids != closing_day_flows[-1].after.observation_ids):
        return None, (), "AMBIGUOUS_CLOSING_DAY_FLOW_ORDER"

    audit = [MetricAudit("opening", period.opening.as_of,
                         period.opening.value_gbp, ZERO, ONE,
                         period.opening.observation_ids,
                         units_outstanding=period.opening.value_gbp)]
    nav = ONE
    units = period.opening.value_gbp
    prior = period.opening.value_gbp
    with localcontext() as ctx:
        ctx.prec = 40
        for day, _, kind, item in events:
            if kind == "daily":
                if prior <= ZERO:
                    return None, (), "UNDEFINED_UNIT_NAV"
                nav *= item.value_gbp / prior
                prior = item.value_gbp
                audit.append(MetricAudit("daily", day, prior, ZERO, +nav,
                                         item.observation_ids,
                                         units_outstanding=+units))
            else:
                if prior <= ZERO:
                    return None, (), "UNDEFINED_UNIT_NAV"
                nav *= item.before.value_gbp / prior
                audit.append(MetricAudit("pre_flow:" + item.flow.event_id,
                    day, item.before.value_gbp, ZERO, +nav,
                    item.before.observation_ids, units_outstanding=+units))
                # Funds create/redeem units at the same valuation, rather than
                # incorrectly counting contributions as investment returns.
                delta_units = item.flow.amount_gbp / nav
                units += delta_units
                if units <= ZERO:
                    return None, (), "UNDEFINED_UNIT_NAV"
                prior = item.after.value_gbp
                audit.append(MetricAudit("post_flow:" + item.flow.event_id,
                    day, prior, item.flow.amount_gbp, +nav,
                    item.after.observation_ids,
                    units_outstanding=+units, units_issued=+delta_units))
        if prior <= ZERO:
            return None, (), "UNDEFINED_UNIT_NAV"
        nav *= period.closing.value_gbp / prior
        audit.append(MetricAudit("closing", period.closing.as_of,
            period.closing.value_gbp, ZERO, +nav,
            period.closing.observation_ids, units_outstanding=+units))
        return +nav, tuple(audit), ""


def evaluate_period(period: PortfolioPeriod) -> PeriodMetrics:
    """Compute exactly what the available GBP facts support, no more.

    XIRR and cash gain require reconciled external flows + two verified
    endpoint values. Exact TWR, annualized TWR and sampled unit-NAV drawdown
    additionally require every pre/post flow boundary and ordered marks.
    """
    if not isinstance(period, PortfolioPeriod):
        return _result("INVALID_PORTFOLIO_PERIOD")
    if (not _approved(period.opening) or not _approved(period.closing) or
            period.opening.currency != period.closing.currency or
            period.opening.value_gbp <= ZERO or
            period.opening.as_of >= period.closing.as_of):
        return _result("UNVERIFIED_VALUATION_ENDPOINTS")
    open_value = period.opening.value_gbp
    close_value = period.closing.value_gbp
    if not _valid_flows(period.flows, period.opening.as_of, period.closing.as_of):
        return _result("INVALID_EXTERNAL_FLOW_LEDGER")
    flows = sorted(period.flows, key=lambda f: (f.occurred_on, f.sequence))
    signed = sum((flow.amount_gbp for flow in flows), ZERO)
    gain = close_value - open_value - signed
    investor_flows = [(period.opening.as_of, -open_value)]
    investor_flows.extend((flow.occurred_on, -flow.amount_gbp) for flow in flows)
    rate = xirr(investor_flows, period.closing.as_of, close_value)
    unit_nav, audit, error = _exact_unit_nav(period, flows)
    if error:
        return _result(error, open_value, close_value, signed, gain, rate)
    cumulative = unit_nav - ONE
    return PeriodMetrics("ok", "", open_value, close_value, signed, gain, rate,
        cumulative, (-ONE if cumulative == -ONE else
                     annualized_return(cumulative, period.opening.as_of,
                                       period.closing.as_of)),
        _drawdown_from_nav([p.unit_nav for p in audit]), audit)


def evaluate_inception(flows: tuple[ExternalFlow, ...],
                       closing: VerifiedValuation) -> InceptionMetrics:
    """End-to-end recorded cash gain and annualized MWR without an opening NAV.

    All actual DEPOSIT/WITHDRAW rows must be supplied, including the first.
    One investment-period cumulative return is only defined when there is
    exactly one deposit and no later external movements.
    """
    if not _approved(closing) or not flows:
        return InceptionMetrics("cannot_compute", "UNVERIFIED_END_OR_NO_FUNDING",
                                None, None, None, None)
    if not isinstance(flows, (tuple, list)) or any(
            not isinstance(f, ExternalFlow) or
            type(f.occurred_on) is not date for f in flows):
        return InceptionMetrics("cannot_compute", "INVALID_EXTERNAL_FLOW_LEDGER",
                                None, None, None, None)
    opening_day = min(f.occurred_on for f in flows)
    if not _valid_flows(flows, opening_day, closing.as_of, include_start=True):
        return InceptionMetrics("cannot_compute", "INVALID_EXTERNAL_FLOW_LEDGER",
                                None, None, None, None)
    ordered = tuple(sorted(flows, key=lambda f: (f.occurred_on, f.sequence)))
    if ordered[0].amount_gbp <= ZERO:
        return InceptionMetrics("cannot_compute", "MISSING_INITIAL_FUNDING",
                                None, None, None, None)
    contributed = sum((f.amount_gbp for f in ordered), ZERO)
    gain = closing.value_gbp - contributed
    rate = xirr([(f.occurred_on, -f.amount_gbp) for f in ordered],
                closing.as_of, closing.value_gbp)
    simple = (closing.value_gbp / ordered[0].amount_gbp - ONE
              if len(ordered) == 1 and ordered[0].occurred_on < closing.as_of
              else None)
    gain_pct = (gain / contributed * PERCENT if contributed > ZERO else None)
    return InceptionMetrics("ok", "", contributed, gain, rate, simple, ordered,
                            gain_pct)


def allocation_drift(values_gbp: Mapping[str, Decimal],
                     weights: Mapping[str, Decimal]) -> tuple[SleeveDrift, ...] | None:
    """Compute arbitrary role weights and percentage-point differences.

    Cash must be an explicitly assigned role when it belongs in the mix.
    No unknown holdings, implied 9Sig safe allocation or silent rounding.
    """
    if (not isinstance(values_gbp, Mapping) or
            not isinstance(weights, Mapping) or
            not values_gbp or not weights or
            set(values_gbp) != set(weights)):
        return None
    if any(not isinstance(role, str) or not role.strip() for role in weights):
        return None
    if any(not _decimal(v) or v < ZERO for v in values_gbp.values()):
        return None
    if any(not _decimal(w) or w < ZERO or w > ONE for w in weights.values()):
        return None
    if sum(weights.values(), ZERO) != ONE:
        return None
    total = sum(values_gbp.values(), ZERO)
    if total <= ZERO:
        return None
    return tuple(SleeveDrift(role, values_gbp[role] / total, weights[role],
        ((values_gbp[role] / total) - weights[role]) * PERCENT)
        for role in sorted(weights))
