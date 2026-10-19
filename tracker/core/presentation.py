"""Strategy-neutral, side-effect-free presentation models for the owner pages.

The Django adapter chooses an owned portfolio and supplies VERIFIED data and
persisted advisory events. Presentation does not evaluate a strategy, change a
financial result, choose instruments, infer a trade or call a broker.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping


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
        ("drawdown", "Sampled drawdown", "%", "Only flow-safe unitised valuations"),
    )
    for key, title, suffix, help_text in fields:
        value = performance.get(key)
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


def signal_display_rows(signals) -> tuple[dict, ...]:
    """Keep historical instructions visible as evidence, not live orders."""
    return tuple({
        "id": signal.pk, "date": signal.as_of,
        "title": signal.title, "message": signal.message,
        "kind": signal.kind,
        "outcome": (signal.get_review_outcome_display()
                    if signal.review_outcome else
                    "Acknowledged" if signal.acknowledged_at else "Not reviewed"),
        "has_proposals": any(a.get("side") in ("BUY", "SELL")
                             for a in (signal.actions or ())),
    } for signal in signals)
