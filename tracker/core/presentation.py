"""Strategy-neutral, side-effect-free presentation models for the owner pages.

The Django adapter chooses an owned portfolio and supplies VERIFIED data and
persisted advisory events. Presentation does not evaluate a strategy, change a
financial result, choose instruments, infer a trade or call a broker.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from tracker.core.metrics import allocation_drift


@dataclass(frozen=True)
class NextStep:
    state: str  # review, blocked, trade, idle
    title: str
    explanation: str
    details: str = ""


@dataclass(frozen=True)
class HoldingRow:
    symbol: str
    role: str
    quantity: Decimal
    price_gbp: Decimal | None
    value_gbp: Decimal | None
    as_of: object | None


def select_next_step(*, signal_kind: str, signal_title: str,
                     signal_message: str, proposed_actions: tuple,
                     price_ready: bool, unavailable_reason: str,
                     next_review_label: str) -> NextStep:
    """A warning is a manual REVIEW, never a disguised executable trade."""
    if signal_kind and signal_kind != "QUARTERLY":
        return NextStep("review", signal_title or "Review an important warning",
                        signal_message or "Check the recorded alert before acting.",
                        unavailable_reason)
    if not price_ready:
        return NextStep("blocked", "An instruction is unavailable",
                        unavailable_reason or "The required verified information is missing.")
    if signal_kind == "QUARTERLY" and any(
            row.get("side") in ("BUY", "SELL") for row in proposed_actions):
        return NextStep("trade", "Review the proposed transactions",
                        "Check the suggested quantities and live broker prices. "
                        "Only you can trade and record actual fills.")
    return NextStep("idle", "Nothing to do right now",
                    "Next planned review: %s. You can record a contribution at any time."
                    % next_review_label)


def account_value(*, net_cash: Decimal, portfolio_value: Decimal | None,
                  prices_verified: bool, has_holdings: bool) -> Decimal | None:
    """A cash-only wallet is actual GBP cash, not a guess at market value."""
    if not has_holdings:
        return net_cash
    return portfolio_value if prices_verified else None


def visible_performance(performance: Mapping) -> tuple[dict, ...]:
    """Metadata keeps different return definitions and windows separate."""
    result = []
    fields = (
        ("xirr", "Money-weighted return", "%", "Annualised since first recorded deposit"),
        ("twr", "Time-weighted return", "%", "Only the verified snapshot period; not since inception"),
        ("drawdown", "Sampled drawdown", "%", "Only supported by evidenced, flow-safe unitised valuations"),
        ("annual_twr", "Annualised time-weighted", "%",
         "Only over the verified time-weighted period"),
    )
    for key, title, suffix, help_text in fields:
        value = (performance.get("annual") if
                 key == "annual_twr" and
                 performance.get("annual_basis") == "verified_snapshot_twr"
                 else None if key == "annual_twr" else performance.get(key))
        result.append({"key": key, "title": title, "value": value,
                       "suffix": suffix, "help": help_text,
                       "available": value is not None})
    return tuple(result)


def holding_rows(report: Mapping, *, roles: Mapping[str, str],
                 show_quotes: bool) -> tuple[HoldingRow, ...]:
    rows = []
    for symbol, quantity in sorted(report["positions"].items()):
        if quantity <= Decimal("0"):
            continue
        mark = report["marks"].get(symbol) if show_quotes else None
        rows.append(HoldingRow(
            symbol, roles.get(symbol, "Other recorded holding"), quantity,
            mark.price if mark else None,
            quantity * mark.price if mark else None,
            mark.as_of if mark else None))
    return tuple(rows)


def ledger_cash_movement(entry) -> Decimal:
    """Display exactly the same cash movement rules as ledger_summary."""
    penny = Decimal("0.01")
    if entry.kind == "DEPOSIT":
        return entry.amount
    if entry.kind == "WITHDRAW":
        return -entry.amount
    if entry.kind == "DIVIDEND":
        return entry.amount
    if entry.kind == "FEE":
        return -entry.amount
    if entry.kind == "BUY":
        return -((entry.quantity * entry.unit_price).quantize(penny) + entry.fee)
    if entry.kind == "SELL":
        return (entry.quantity * entry.unit_price).quantize(penny) - entry.fee
    raise ValueError("Unknown ledger kind")


def ledger_display_rows(entries) -> tuple[dict, ...]:
    """An event list, not a guessed balance history reconstructed backwards."""
    return tuple({
        "date": entry.occurred_on,
        "kind": entry.get_kind_display(),
        "symbol": entry.symbol,
        "quantity": entry.quantity if entry.kind in ("BUY", "SELL") else None,
        "unit_price": entry.unit_price if entry.kind in ("BUY", "SELL") else None,
        "cash_change": ledger_cash_movement(entry),
        "fee": entry.fee if entry.kind in ("BUY", "SELL") else None,
        "note": entry.note,
        "id": entry.pk,
    } for entry in entries)


def signal_display_rows(signals, *, executions=None, partial_counts=None) -> tuple[dict, ...]:
    """Historical event presentation, with separately confirmed manual fills.

    An execution marker certifies an owner-recorded act but it cannot justify
    reconstructed execution prices; users inspect actual ledger rows instead.
    """
    executions = executions or {}
    partial_counts = partial_counts or {}
    return tuple({
        "id": signal.pk, "date": signal.as_of,
        "title": signal.title, "message": signal.message,
        "kind": signal.kind,
        "outcome": (signal.get_review_outcome_display()
                    if signal.review_outcome else
                    "Partial: %s actual fill(s) recorded" % partial_counts[signal.pk]
                    if partial_counts.get(signal.pk) else
                    "Acknowledged" if signal.acknowledged_at else "Not reviewed"),
        "executed_on": executions.get(signal.pk),
        "has_proposals": any(a.get("side") in ("BUY", "SELL")
                             for a in (signal.actions or ())),
    } for signal in signals)


@dataclass(frozen=True)
class AllocationRow:
    role: str
    actual_pct: Decimal
    reference_pct: Decimal
    drift_pp: Decimal
    value_gbp: Decimal


def allocation_comparison(report: Mapping, *, instrument_roles: Mapping[str, str],
                          fixed_targets: Mapping[str, Decimal],
                          verified: bool) -> tuple[AllocationRow, ...] | None:
    """Presentation of today's sleeves against a *labelled baseline reference*.

    Used only after a trustworthy complete portfolio valuation; no silent
    dropping of unmapped holdings or pretending 60/40 is a quarterly signal.
    Cash is an explicit 0% reference role.
    """
    if not verified or not instrument_roles or not fixed_targets:
        return None
    try:
        cash = report["cash"]
        positions = report["positions"]
        marks = report["marks"]
        total = report["value"]
        if (not isinstance(cash, Decimal) or cash < 0 or
                not isinstance(total, Decimal) or total <= 0 or
                set(fixed_targets) != set(instrument_roles.values())):
            return None
        actual = {role: Decimal("0") for role in fixed_targets}
        actual["Cash"] = cash
        for symbol, quantity in positions.items():
            if quantity <= 0:
                if quantity < 0:
                    return None
                continue
            role = instrument_roles.get(symbol)
            mark = marks.get(symbol)
            if role not in actual or mark is None or mark.price <= 0:
                return None
            actual[role] += quantity * mark.price
        # The accounting summary rounds the complete GBP portfolio to
        # pence. Compare the same penny-rounded sum rather than demanding
        # exact equality of fractional-share price products.
        if sum(actual.values(), Decimal("0")).quantize(Decimal("0.01")) != total:
            return None
        targets = {**fixed_targets, "Cash": Decimal("0")}
        drift = allocation_drift(actual, targets)
        if drift is None:
            return None
        return tuple(AllocationRow(
            role=row.role, actual_pct=row.actual_weight * 100,
            reference_pct=row.target_weight * 100,
            drift_pp=row.drift_percentage_points, value_gbp=actual[row.role])
            for row in drift)
    except (KeyError, TypeError, AttributeError, ArithmeticError):
        return None
