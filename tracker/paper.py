"""Start a genuinely isolated, virtual 9Sig account using verified closing prices.

The virtual buys are explicitly labelled as paper fills; no broker connection,
broker API calls or automatic future executions exist. Starting balance is £1,000.
"""
from decimal import Decimal, ROUND_DOWN
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from datetime import timedelta

from .market_signals import sync_ticker
from .models import Account, LedgerEntry, PriceMark, StrategyInstance, StrategyPreset, StrategyValueSnapshot
from .services import validate_and_record, ledger_summary

PAPER_NAME = "9Sig Paper Trial"
EQUITY = "LQQ3.L"
SAFE = "ERNS.L"
INITIAL_CASH = Decimal("1000.00")
MONTHLY_PLAN = Decimal("500.00")
SHARE_PRECISION = Decimal("0.00000001")


class PaperSetupUnavailable(Exception):
    pass


def _checked_closes():
    quote_map = {}
    for ticker in (EQUITY, SAFE):
        quote = sync_ticker(ticker)
        if not quote or quote.currency != "GBP" or quote.price <= 0:
            raise PaperSetupUnavailable(
                "Yahoo GBP closing prices are unavailable for " + ticker +
                ". No virtual trades were recorded."
            )
        if (timezone.localdate() - quote.as_of).days not in range(0, 9):
            raise PaperSetupUnavailable(
                "The most recent market close is too old for " + ticker +
                ". No virtual trades were recorded."
            )
        quote_map[ticker] = quote
    # The virtual two-leg purchase is booked at one common daily market close.
    if quote_map[EQUITY].as_of != quote_map[SAFE].as_of:
        raise PaperSetupUnavailable(
            "The two trading lines have different closing dates. Retry after their "
            "daily prices align; no virtual trade was recorded."
        )
    return quote_map


def create_paper_trial(owner):
    existing = StrategyInstance.objects.filter(
        owner=owner, account__kind="PAPER",
        account__name=PAPER_NAME, name=PAPER_NAME,
    ).first()
    if existing:
        return existing, False

    # Network happens before DB transaction, so a provider failure never leaves
    # an empty account or half-entered fake portfolio behind.
    quotes = _checked_closes()

    with transaction.atomic():
        # Serialize concurrent double-clicks for a specific user.
        locked_owner = get_user_model().objects.select_for_update().get(pk=owner.pk)
        existing = StrategyInstance.objects.filter(
            owner=locked_owner, account__kind="PAPER",
            account__name=PAPER_NAME, name=PAPER_NAME,
        ).first()
        if existing:
            return existing, False
        preset = StrategyPreset.objects.get(
            slug="nasdaq-quarterly-target", active=True)
        account = Account.objects.create(
            owner=locked_owner, name=PAPER_NAME, kind="PAPER")
        strategy = StrategyInstance.objects.create(
            owner=locked_owner, account=account, preset=preset,
            name=PAPER_NAME, monthly_target=MONTHLY_PLAN,
            equity_symbol=EQUITY, safe_symbol=SAFE,
            market_data_enabled=True, contribution_alerts=False,
        )
        day = quotes[EQUITY].as_of
        validate_and_record(strategy, LedgerEntry(
            kind=LedgerEntry.DEPOSIT, occurred_on=day, amount=INITIAL_CASH,
            note="PAPER ONLY: virtual £1,000 seed, not actual money",
        ))
        for ticker, allocation in ((EQUITY, Decimal("600")), (SAFE, Decimal("400"))):
            price = quotes[ticker].price
            qty = (allocation / price).quantize(SHARE_PRECISION, rounding=ROUND_DOWN)
            if qty <= 0:
                raise PaperSetupUnavailable("Market price cannot fund this virtual allocation")
            validate_and_record(strategy, LedgerEntry(
                kind=LedgerEntry.BUY, occurred_on=day,
                symbol=ticker, quantity=qty, unit_price=price,
                note="PAPER ONLY: not a broker execution, fractional virtual shares",
            ))
            PriceMark.objects.create(
                strategy=strategy, symbol=ticker,
                price=price, as_of=day, source="yahoo",
            )
        summary = ledger_summary(strategy)
        if summary["value"] is None:
            raise PaperSetupUnavailable("The new paper account could not be valued")
        StrategyValueSnapshot.objects.create(
            strategy=strategy, as_of=day,
            total_value=summary["value"],
            net_contributed=summary["net_contributed"],
        )
        return strategy, True
