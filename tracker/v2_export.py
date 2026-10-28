"""One download of everything the owner has recorded, as plain CSV files in a ZIP.

Read-only and owner-scoped. Shows the same figures the pages show, plus the fixed
rules each strategy follows, so the working can be checked in any spreadsheet.
"""
import csv
import io
import json
import zipfile

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from .core.presentation import NOT_ADVICE
from .models import MarketObservation, SignalFill, StrategyInstance, StrategySignal
from .strategies.instruments import symbols as fund_symbols
from .strategies.registry import describe_strategy
from .v2_ledger_core import effective_entries
from .v2_ledger_page import _safe

README = """Wealtharr export, {when}

One folder per strategy:
  ledger.csv    every deposit, trade, dividend, fee and split you recorded (deleted entries are marked)
  reviews.csv   each rules output the app saved, and what you did with it
  fills.csv     the trades you recorded against a review
  rules.csv     the fixed rules and pinned funds this strategy follows
  prices.csv    the stored closing prices for its funds

{notice}

Past results are not a guide to future results.
"""


def _csv(header, rows):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    for row in rows:
        writer.writerow([_safe(cell) if isinstance(cell, str) else cell for cell in row])
    return out.getvalue()


def _ledger(strategy):
    unit = strategy.account.currency.lower()
    rows = [(e.pk, e.version, "soft_deleted" if e.deleted else "active", e.occurred_on.isoformat(),
             e.kind, e.symbol, e.quantity, e.unit_price, e.amount, e.fee, e.note,
             "manual_rebalance" if e.linked_fill else "manual", e.created_at.isoformat())
            for e in effective_entries(strategy, show_deleted=True)]
    return _csv(["entry_id", "revision", "status", "occurred_on", "kind", "symbol", "quantity",
                 "unit_price_" + unit, "amount_" + unit, "fee_" + unit, "note", "source", "created_at"], rows)


def _reviews(strategy):
    rows = [(s.as_of.isoformat(), s.kind, s.title, s.message, s.strategy_version, s.review_outcome,
             s.review_note, s.created_at.isoformat(), json.dumps(s.actions, sort_keys=True))
            for s in StrategySignal.objects.filter(strategy=strategy).order_by("as_of", "pk")]
    return _csv(["as_of", "kind", "title", "message", "rules_version", "outcome", "note",
                 "created_at", "actions_json"], rows)


def _fills(strategy):
    rows = [(f.signal_id, f.ledger_entry.occurred_on.isoformat(), f.ledger_entry.kind, f.ledger_entry.symbol,
             f.ledger_entry.quantity, f.ledger_entry.unit_price, f.ledger_entry.fee)
            for f in SignalFill.objects.filter(strategy=strategy).select_related("ledger_entry").order_by("pk")]
    return _csv(["review_id", "occurred_on", "kind", "symbol", "quantity", "unit_price", "fee"], rows)


def _rules(strategy):
    key = strategy.strategy_key or strategy.preset.slug
    desc = describe_strategy(key)
    targets = dict(desc.role_targets) if desc is not None and getattr(desc, "role_targets", None) else {}
    roles = (strategy.resolved_instruments or {}).get("roles", {})
    rows = [("strategy", key, ""), ("rules_version", strategy.strategy_version, ""),
            ("mapping_version", strategy.mapping_version, ""), ("platform", strategy.platform_code, ""),
            ("account_type", strategy.account_type_code, "")]
    for role, info in sorted(roles.items()):
        rows.append(("fund", role, "%s %s" % (info.get("yahoo_ticker", ""), info.get("isin", "")) ))
    for role, weight in sorted(targets.items()):
        rows.append(("target_weight", role, weight))
    return _csv(["item", "name", "value"], rows)


def _prices(strategy):
    rows = MarketObservation.objects.filter(
        symbol__in=fund_symbols(strategy), currency=strategy.account.currency,
    ).order_by("symbol", "as_of").values_list("symbol", "as_of", "price", "currency", "source", "raw_unit_verified")
    return _csv(["symbol", "as_of", "close", "currency", "source", "unit_verified"],
                [(s, d.isoformat(), p, c, src, v) for s, d, p, c, src, v in rows])


@login_required
@require_GET
def export_all(request):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", README.format(
            when=timezone.localdate().isoformat(), notice=NOT_ADVICE))
        owned = StrategyInstance.objects.select_related("account", "preset").filter(
            owner=request.user, account__owner=request.user).exclude(account__kind="PAPER")
        for strategy in owned:
            folder = "strategy-%d/" % strategy.pk
            archive.writestr(folder + "ledger.csv", _ledger(strategy))
            archive.writestr(folder + "reviews.csv", _reviews(strategy))
            archive.writestr(folder + "fills.csv", _fills(strategy))
            archive.writestr(folder + "rules.csv", _rules(strategy))
            archive.writestr(folder + "prices.csv", _prices(strategy))
    response = HttpResponse(buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = 'attachment; filename="wealtharr-export-%s.zip"' % timezone.localdate()
    return response
