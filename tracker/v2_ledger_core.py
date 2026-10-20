"""Version-aware ledger projection and pure cash/position replay.

LedgerEntry remains untouched; each edit/delete/restore appends one LedgerRevision.
This projection is used by the existing ledger_summary so corrections never
double-count contributions or positions. No broker connection or market quote.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal as D
from hashlib import sha256
import json

from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import LedgerEntry, LedgerRevision, RebalanceExecution, SignalFill, BrokerImportRow


ZERO = D("0")
PENNY = D("0.01")
QTY_QUANT = D("0.00000001")
ENTRY_FIELDS = ("occurred_on", "kind", "symbol", "quantity", "unit_price",
                "amount", "fee", "note")
MONEY_KINDS = {"DEPOSIT", "WITHDRAW", "DIVIDEND", "INTEREST", "FEE"}
TRADE_KINDS = {"BUY", "SELL"}


@dataclass(frozen=True)
class EffectiveEntry:
    pk: int
    strategy_id: int
    occurred_on: date
    kind: str
    symbol: str
    quantity: D
    unit_price: D
    amount: D
    fee: D
    note: str
    created_at: object
    version: int = 0
    deleted: bool = False
    imported: bool = False
    linked_fill: bool = False

    @property
    def financial_value(self):
        return (self.quantity * self.unit_price).quantize(PENNY)


def snapshot(row):
    """JSON-safe, complete financial state of a real or effective entry."""
    return {
        "occurred_on": row.occurred_on.isoformat(),
        "kind": row.kind,
        "symbol": row.symbol,
        "quantity": str(row.quantity),
        "unit_price": str(row.unit_price),
        "amount": str(row.amount),
        "fee": str(row.fee),
        "note": row.note,
    }


def _parse(row, payload, version=0, *, deleted=False, imported=False, linked=False):
    return EffectiveEntry(
        pk=row.pk, strategy_id=row.strategy_id,
        occurred_on=date.fromisoformat(payload["occurred_on"]),
        kind=payload["kind"], symbol=payload["symbol"],
        quantity=D(payload["quantity"]), unit_price=D(payload["unit_price"]),
        amount=D(payload["amount"]), fee=D(payload["fee"]),
        note=payload.get("note", ""), created_at=row.created_at,
        version=version, deleted=deleted, imported=imported, linked_fill=linked,
    )


def effective_entries(strategy, *, show_deleted=False):
    originals = list(strategy.ledger.all().order_by("occurred_on", "pk"))
    revisions = LedgerRevision.objects.filter(
        strategy=strategy
    ).order_by("entry_id", "version")
    latest = {}
    for revision in revisions:
        latest[revision.entry_id] = revision
    imported = set(BrokerImportRow.objects.filter(
        strategy=strategy).values_list("ledger_entry_id", flat=True))
    fills = set(SignalFill.objects.filter(
        strategy=strategy).values_list("ledger_entry_id", flat=True))
    effective = []
    for original in originals:
        latest_revision = latest.get(original.pk)
        if latest_revision:
            version = latest_revision.version
            data = latest_revision.after
            deleted = data is None
            if deleted:
                if not show_deleted:
                    continue
                data = latest_revision.before
                # A delete's before always points to the previously effective state.
                if data is None:
                    continue
        else:
            version = 0
            data = snapshot(original)
            deleted = False
        effective.append(_parse(
            original, data, version, deleted=deleted,
            imported=original.pk in imported, linked=original.pk in fills,
        ))
    return sorted(effective, key=lambda x: (x.occurred_on, x.pk))


def find_entry(strategy, entry_id, *, include_deleted=True):
    return next((
        e for e in effective_entries(strategy, show_deleted=include_deleted)
        if e.pk == entry_id
    ), None)


def replay(entries, *, strict=True):
    """Validate *every* historical intermediate cash/holding balance."""
    cash = ZERO
    funded = ZERO
    holdings = {}
    for row in sorted(
        (e for e in entries if not e.deleted),
        key=lambda e: (e.occurred_on, e.pk),
    ):
        if not isinstance(row.occurred_on, date) or row.occurred_on > timezone.localdate():
            raise ValidationError("Ledger entries must have real, non-future dates.")
        if any(not isinstance(v, D) or not v.is_finite() or v < ZERO
               for v in (row.quantity, row.unit_price, row.amount, row.fee)):
            raise ValidationError("Ledger contains an invalid amount, price, share quantity or fee.")
        if row.kind in MONEY_KINDS:
            if row.amount <= ZERO or row.quantity != ZERO or row.unit_price != ZERO or row.fee != ZERO:
                raise ValidationError("Cash entries require a positive GBP amount and no shares or price.")
            if row.kind == "DEPOSIT":
                cash += row.amount
                funded += row.amount
            elif row.kind == "WITHDRAW":
                cash -= row.amount
                funded -= row.amount
            elif row.kind in ("DIVIDEND", "INTEREST"):
                cash += row.amount
            elif row.kind == "FEE":
                cash -= row.amount
        elif row.kind in TRADE_KINDS:
            if (not row.symbol or row.quantity <= ZERO or row.unit_price <= ZERO or
                    row.amount != ZERO):
                raise ValidationError("Trades require mapped shares and a positive execution price.")
            gross = (row.quantity * row.unit_price).quantize(PENNY)
            previous = holdings.get(row.symbol, ZERO)
            if row.kind == "BUY":
                cash -= gross + row.fee
                holdings[row.symbol] = previous + row.quantity
            else:
                cash += gross - row.fee
                holdings[row.symbol] = previous - row.quantity
        else:
            raise ValidationError("Unsupported ledger event type.")
        if strict and cash < ZERO:
            raise ValidationError("This change would make cash negative at an earlier transaction.")
        if strict and any(v < ZERO for v in holdings.values()):
            raise ValidationError("This change would sell more shares than were held at that date.")
    return {
        "cash": cash.quantize(PENNY),
        "net_contributed": funded.quantize(PENNY),
        "positions": {k: v for k, v in sorted(holdings.items()) if v != ZERO},
    }


def with_proposed_change(strategy, entry_id, replacement):
    """Hypothetical projection; replacement None means soft delete."""
    entries = effective_entries(strategy, show_deleted=True)
    if entry_id is None:
        raise ValidationError("A saved entry is required for revision preview.")
    previous = next((x for x in entries if x.pk == entry_id), None)
    if previous is None:
        raise ValidationError("The entry is missing or belongs to another owner.")
    updated = []
    for row in entries:
        if row.pk != entry_id:
            updated.append(row)
        elif replacement is not None:
            base = strategy.ledger.get(pk=entry_id)
            updated.append(_parse(
                base, replacement, row.version + 1,
                imported=row.imported, linked=row.linked_fill,
            ))
    return previous, replay(updated, strict=True)


def evidence_impact(strategy, changed_from):
    signals = list(strategy.signals.filter(
        as_of__gte=changed_from).order_by("as_of", "pk"))
    baselines = list(strategy.baselines.filter(as_of__gte=changed_from))
    valuations = list(strategy.value_snapshots.filter(as_of__gte=changed_from))
    completed = set(RebalanceExecution.objects.filter(
        strategy=strategy, signal_id__in=[s.pk for s in signals]
    ).values_list("signal_id", flat=True))
    confirmed = [s for s in signals if s.pk in completed or s.review_outcome == "CONFIRMED"]
    return {
        "periods": [
            {"as_of": signal.as_of.isoformat(), "title": signal.title,
             "confirmed": signal in confirmed, "signal_id": signal.pk}
            for signal in signals
        ],
        "baseline_ids": [b.pk for b in baselines],
        "valuation_dates": [v.as_of.isoformat() for v in valuations],
        "requires_recheck": bool(signals or baselines or valuations),
        "blocks_change": bool(confirmed or baselines or valuations),
    }


def state_digest(strategy):
    """Detect changes between authenticated preview and atomic confirmation."""
    rows = [
        (entry.pk, entry.version, entry.deleted, snapshot(entry))
        for entry in effective_entries(strategy, show_deleted=True)
    ]
    return sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def linked_source(strategy, entry_id):
    return (
        BrokerImportRow.objects.filter(strategy=strategy, ledger_entry_id=entry_id).exists()
        or SignalFill.objects.filter(strategy=strategy, ledger_entry_id=entry_id).exists()
    )


def latest_deleted_payload(strategy, entry_id):
    """Restore the most recent earlier effective version exactly, not a guess."""
    revisions = list(LedgerRevision.objects.filter(
        strategy=strategy, entry_id=entry_id
    ).order_by("-version"))
    if not revisions or revisions[0].after is not None:
        raise ValidationError("Only a deleted entry can be restored.")
    for revision in revisions[1:]:
        if revision.after is not None:
            return revision.after
    return snapshot(strategy.ledger.get(pk=entry_id))
