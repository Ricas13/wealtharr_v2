"""History data: owner-isolated, read-only evidence behind Performance and Overview.

Saved GBP snapshots are records, not inherently approved valuations. The
source schema omits per-price vendor/raw-unit evidence. Never label such
invested snapshots as verified or turn them into XIRR/TWR calculations.
A position-free GBP cash endpoint can, however, be independently replayed
from real owner ledger entries. Metrics reuse the released Decimal engine.

No requests, no price syncs and no database writes happen here.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from .core.metrics import (
    ExternalFlow, PortfolioPeriod, VerifiedValuation,
    evaluate_inception, evaluate_period,
)
from .models import MarketObservation, YahooSync
from .v2_ledger_core import effective_entries, replay
from .strategies.instruments import symbols as fund_symbols

ZERO = Decimal("0")
WINDOWS = (("all", "All"), ("ytd", "YTD"), ("1y", "1 year"),
           ("3y", "3 years"), ("5y", "5 years"))
RETURN_HOLD = (
    "Historical investment values have no saved raw vendor quote-unit and "
    "source identities. They are recorded snapshots, not independently "
    "certified valuations. XIRR, TWR and drawdown are withheld."
)




@dataclass(frozen=True)
class HistoryPoint:
    as_of: date
    value_gbp: Decimal
    net_contributed_gbp: Decimal
    origin: str
    certified: bool
    reason: str
    observation_ids: tuple[str, ...] = ()


def _ownership(strategy):
    return strategy.owner_id == strategy.account.owner_id


def _blocked(strategy):
    if not _ownership(strategy):
        return "The account owner and strategy owner differ."
    from .core.brokers import CURRENCY_SYMBOLS
    if strategy.account.currency not in CURRENCY_SYMBOLS:
        return "History requires an account in a supported currency and exact currency data."
    return ""


def _state_as_of(entries, day):
    return replay([entry for entry in entries if entry.occurred_on <= day])


def _cash_evidence(strategy, state, day, entries):
    """An actual cash-only ledger is a verified GBP NAV with no quote assumptions."""
    if state["positions"] or not entries:
        return None
    relevant = [e for e in entries if e.occurred_on <= day]
    if not relevant or day < relevant[0].occurred_on or state["cash"] < ZERO:
        return None
    # Ledger entry identities, dates and revision versions are audit provenance.
    return VerifiedValuation(
        as_of=day, value_gbp=state["cash"], verified=True, currency=strategy.account.currency,
        observation_ids=tuple(
            "owner-ledger:%s:%s:%s" % (strategy.pk, e.pk, e.version)
            for e in relevant
        ),
    )


def _invested_evidence(strategy, state, day, entries, snapshot_value, *, evidence=None):
    """Source-checked exact-date GBP quote proof, not broker eligibility proof.

    The original Yahoo raw unit and price must have been captured by sync_ticker
    on that exact trading day; legacy normalized-only rows remain unapproved.
    """
    currency = strategy.account.currency
    from .core.catalogue import expected_quote_units
    from .strategies.instruments import symbols
    units_by_symbol = expected_quote_units()
    funds = symbols(strategy)
    # Only catalogued GBP/GBX trading lines of this strategy count as evidence.
    expected = {
        s: units_by_symbol[s] + (Decimal("100") if "GBX" in units_by_symbol[s] else Decimal("1"),)
        for s in funds if s in units_by_symbol and set(units_by_symbol[s]) <= (
            {"GBp", "GBX", "GBP"} if currency == "GBP" else {currency})
    }
    # USD-quoted lines: the raw close divided by that day's GBP/USD close.
    from .market_data import fx_rates, usd_holding_symbols, usd_to_gbp
    rate = (evidence["rates"].get(day) if evidence is not None
            else fx_rates([day]).get(day))
    if currency == "GBP":
        expected.update({s: ("USD", None) for s in funds if s in usd_holding_symbols() and rate})
    if not expected or len(expected) != len(funds) or not state["positions"]:
        return None
    value = state["cash"]
    ids = []
    for symbol, qty in state["positions"].items():
        if qty <= ZERO or symbol not in expected:
            return None
        if (symbol in evidence["held"] if evidence is not None else
                YahooSync.objects.filter(
                    symbol=symbol, last_error__startswith="PRICE_HOLD:").exists()):
            return None
        obs = (evidence["observations"].get((symbol, day)) if evidence is not None
               else MarketObservation.objects.filter(
                   symbol=symbol, as_of=day, currency=currency,
                   source="yahoo", raw_unit_verified=True,
               ).first())
        if obs is None or not isinstance(obs.raw_close, Decimal) or obs.raw_close <= ZERO:
            return None
        allowed = expected[symbol]
        units = allowed[:-1]
        factor = allowed[-1]
        converted = (usd_to_gbp(obs.raw_close, rate) if factor is None
                     else (obs.raw_close / factor).quantize(Decimal("0.00000001")))
        if (obs.raw_quote_unit not in units or
                not obs.price.is_finite() or obs.price <= ZERO or converted != obs.price):
            return None
        value += qty * obs.price
        ids.append("yahoo-raw:%s:%s:%s:%s" % (
            obs.pk, symbol, obs.raw_quote_unit, obs.as_of.isoformat()
        ))
    if value.quantize(Decimal("0.01")) != snapshot_value:
        return None
    ids.extend(
        "owner-ledger:%s:%s:%s" % (strategy.pk, entry.pk, entry.version)
        for entry in entries if entry.occurred_on <= day
    )
    return VerifiedValuation(
        as_of=day, value_gbp=snapshot_value, verified=True,
        currency=currency, observation_ids=tuple(ids),
    )


def _flow_rows(entries, until):
    result = []
    for sequence, event in enumerate(e for e in entries if
            e.occurred_on <= until and e.kind in ("DEPOSIT", "WITHDRAW")):
        signed = event.amount if event.kind == "DEPOSIT" else -event.amount
        result.append(ExternalFlow(
            event_id="entry:%s:v%s" % (event.pk, event.version),
            occurred_on=event.occurred_on,
            amount_gbp=signed, sequence=sequence,
        ))
    return tuple(result)


def _records(strategy, entries):
    """Never back-fill absent quotes; classify saved data instead of trusting it."""
    # Bulk-load the exact same evidence used by the previous per-day checks.
    # Historic charts can have thousands of snapshots; querying one mark and
    # one FX row per snapshot was a costly N+1 pattern on Overview and History.
    from .market_data import fx_rates, usd_holding_symbols
    snapshots = list(strategy.value_snapshots.order_by("as_of", "pk"))
    days = [row.as_of for row in snapshots if row.as_of <= timezone.localdate()]
    held_symbols = set(fund_symbols(strategy))
    evidence = {
        "observations": {
            (obs.symbol, obs.as_of): obs for obs in MarketObservation.objects.filter(
                symbol__in=held_symbols, as_of__in=days,
                currency=strategy.account.currency,
                source="yahoo", raw_unit_verified=True,
            )
        } if days and held_symbols else {},
        "held": set(YahooSync.objects.filter(
            symbol__in=held_symbols, last_error__startswith="PRICE_HOLD:",
        ).values_list("symbol", flat=True)) if held_symbols else set(),
        "rates": (fx_rates(days) if strategy.account.currency == "GBP"
                  and held_symbols.intersection(usd_holding_symbols()) and days else {}),
    }
    points = []
    for record in snapshots:
        if record.as_of > timezone.localdate():
            continue
        try:
            state = _state_as_of(entries, record.as_of)
        except (ValidationError, ArithmeticError):
            points.append(HistoryPoint(
                record.as_of, record.total_value, record.net_contributed,
                "Recorded historic snapshot", False, "Ledger cannot be reconciled"))
            continue
        if (record.total_value < ZERO or
                record.net_contributed != state["net_contributed"]):
            reason = "Snapshot conflicts with recorded cash flows."
            accepted = None
        else:
            accepted = _cash_evidence(strategy, state, record.as_of, entries)
            if accepted and accepted.value_gbp != record.total_value:
                accepted = None
            if not accepted:
                accepted = _invested_evidence(
                    strategy, state, record.as_of, entries, record.total_value,
                    evidence=evidence,
                )
            reason = (
                "Reproduced from actual %s cash ledger (no positions)." % strategy.account.currency
                if accepted and not state["positions"] else
                "Reproduced from dated owner holdings plus captured Yahoo raw GBX/GBP quote evidence."
                if accepted else RETURN_HOLD
            )
        points.append(HistoryPoint(
            record.as_of, record.total_value, record.net_contributed,
            ("Source-checked portfolio close"
             if accepted and state["positions"] else "Recorded portfolio snapshot"),
            bool(accepted), reason, accepted.observation_ids if accepted else ()))
    # A real current cash-only balance is independently derivable even when
    # no price snapshot exists; it is explicitly labelled ledger, not a quote.
    if entries:
        day = timezone.localdate()
        try:
            state = _state_as_of(entries, day)
        except (ValidationError, ArithmeticError):
            state = None
        if state:
            approved = _cash_evidence(strategy, state, day, entries)
            if approved and not any(p.as_of == day for p in points):
                points.append(HistoryPoint(
                    day, state["cash"], state["net_contributed"],
                    "Replayed ledger cash", True,
                    "Cash only; no securities or market quotes assumed",
                    approved.observation_ids,
                ))
    return tuple(sorted(points, key=lambda p: p.as_of))


def _selected_window(request):
    key = request.GET.get("range", "all")
    return key if key in dict(WINDOWS) else "all"


def _window_start(kind, today):
    if kind == "all":
        return date(1900, 1, 1)
    if kind == "ytd":
        return date(today.year, 1, 1)
    years = {"1y": 1, "3y": 3, "5y": 5}[kind]
    try:
        return today.replace(year=today.year-years)
    except ValueError:
        return today.replace(year=today.year-years, month=2, day=28)


def build_history(strategy, *, span="all"):
    """Purely present stored owner facts; no external market calls."""
    reason = _blocked(strategy)
    if reason:
        return {
            "status": "blocked", "reason": reason, "currency": strategy.account.currency,
            "points": (), "all_points": (), "last_point": None,
            "latest_certified": None, "inception": None, "period": None,
            "actual_net_funding": None, "ledger_cash": None,
            "flows": (), "range": span, "range_start": None,
        }
    entries = tuple(effective_entries(strategy))
    try:
        state = replay(entries)
        points = _records(strategy, entries)
    except (ValidationError, ArithmeticError):
        return {
            "status": "blocked", "reason": "Actual historical cash or share ledger does not reconcile.",
            "points": (), "all_points": (), "last_point": None,
            "latest_certified": None, "inception": None, "period": None,
            "actual_net_funding": None, "ledger_cash": None,
            "flows": (), "range": span, "range_start": None,
        }
    today = timezone.localdate()
    range_start = _window_start(span, today)
    shown = tuple(p for p in points if p.as_of >= range_start)
    latest = points[-1] if points else None
    certified = next((p for p in reversed(points) if p.certified), None)
    # Do not select an old certified cash day as the current value if newer
    # invested observations or real transactions now exist.
    certified = (certified if certified and latest and
                 certified.as_of == latest.as_of and
                 not any(e.occurred_on > certified.as_of for e in entries)
                 else None)
    flows = _flow_rows(entries, latest.as_of) if latest else ()
    inception = None
    period = None
    if certified:
        closing = VerifiedValuation(
            certified.as_of, certified.value_gbp, certified.observation_ids, True,
        )
        inception = evaluate_inception(flows, closing)
        # Period metrics must be anchored by an earlier accepted source value.
        previous = next((p for p in points if p.certified and
                         p.as_of >= range_start and p.as_of < certified.as_of), None)
        if previous:
            opening = VerifiedValuation(
                previous.as_of, previous.value_gbp, previous.observation_ids, True,
            )
            in_period = tuple(f for f in flows if
                              previous.as_of < f.occurred_on <= certified.as_of)
            period = evaluate_period(PortfolioPeriod(opening, closing, in_period))
    status = (
        "no_history" if not points else
        "verified_market" if certified and certified.origin == "Source-checked portfolio close" else
        "verified_cash" if certified else "recorded_only"
    )
    return {
        "status": status,
        "currency": strategy.account.currency,
        "reason": (
            "No verified valuation dates are recorded." if not points else
            "Exact-date Yahoo raw quote units and dated holdings independently reproduce the saved %s valuation." % strategy.account.currency
            if certified and status == "verified_market" else
            "Ledger-backed cash-only endpoint; market investment values remain separately gated."
            if certified else RETURN_HOLD
        ),
        "points": shown, "all_points": points, "last_point": latest,
        "latest_certified": certified, "inception": inception, "period": period,
        "actual_net_funding": state["net_contributed"],
        "ledger_cash": state["cash"], "flows": flows,
        "xirr_pct": inception.xirr_annual * Decimal("100") if inception and inception.xirr_annual is not None else None,
        "twr_pct": period.cumulative_return * Decimal("100") if period and period.cumulative_return is not None else None,
        "drawdown_pct": period.max_drawdown * Decimal("100") if period and period.max_drawdown is not None else None,
        "range": span, "range_start": range_start,
    }
