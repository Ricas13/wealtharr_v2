"""Evidence-backed owner tracking lines, keyed by strategy, country, wrapper and broker.

This registry is deliberately small. Unsupported combinations fail closed and
never silently substitute a security with another ISIN or price currency.
'Broker listed' evidence is for Trading 212 INVEST; individual Stocks ISA
availability and appropriateness are NOT guaranteed.
"""
from dataclasses import dataclass

STRATEGY_9SIG = "nasdaq-quarterly-target"
COUNTRY_CHOICES = (("GB", "United Kingdom"),)
BROKER_CHOICES = (("TRADING212", "Trading 212"), ("OTHER", "Other broker (not yet supported)"))


class InstrumentMappingUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class Instrument:
    name: str
    broker_ticker: str
    yahoo_ticker: str
    isin: str
    currency: str
    listing: str
    allocation_pct: int
    issuer_url: str
    broker_evidence_url: str
    exposure: str


@dataclass(frozen=True)
class StrategyInstrumentProfile:
    strategy_slug: str
    country: str
    account_type: str
    broker: str
    equity: Instrument
    safe: Instrument
    model_warning: str
    broker_warning: str

    @property
    def instruments(self):
        return (self.equity, self.safe)


EQUITY_UK = Instrument(
    name="WisdomTree NASDAQ 100 3x Daily Leveraged",
    broker_ticker="LQQ3",
    yahoo_ticker="LQQ3.L",
    isin="IE00BLRPRL42",
    currency="GBP",
    listing="London Stock Exchange, GBX (pence) line; normalized to GBP in Wealtharr",
    allocation_pct=60,
    issuer_url="https://www.wisdomtree.com/gb/products/equities/wisdomtree-nasdaq-100-3x-daily-leveraged",
    broker_evidence_url="https://www.trading212.com/trading-instruments/invest/LQQ3.GB",
    exposure="3x DAILY Nasdaq-100 exposure; an ETP, not a UCITS ETF or the US TQQQ fund",
)
SAFE_UK = Instrument(
    name="iShares GBP Ultrashort Bond UCITS ETF (Dist)",
    broker_ticker="ERNS",
    yahoo_ticker="ERNS.L",
    isin="IE00BCRY6441",
    currency="GBP",
    listing="London Stock Exchange GBP line",
    allocation_pct=40,
    issuer_url="https://www.ishares.com/uk/individual/en/products/258120/ishares-ultrashort-bond-ucits-etf",
    broker_evidence_url="https://www.trading212.com/trading-instruments/invest/ERNS.GB",
    exposure="GBP ultrashort investment-grade debt; not the US AGG bond benchmark",
)

UK_WARNING = (
    "This is a UK-accessible implementation of a US 9Sig-style model, not "
    "an exact TQQQ/AGG replication. Daily leveraged ETP compounding, fees, "
    "FX exposure and ultrashort bond returns can change strategy results."
)
BROKER_WARNING = (
    "Both issuers indicate UK ISA eligibility and both instruments have been "
    "listed in Trading 212's Invest catalogue. Their availability in your "
    "specific Stocks & Shares ISA and any required leveraged-ETP "
    "appropriateness assessment must be checked inside Trading 212."
)

SUPPORTED_PROFILES = {
    (STRATEGY_9SIG, "GB", kind, "TRADING212"):
        StrategyInstrumentProfile(
            STRATEGY_9SIG, "GB", kind, "TRADING212",
            EQUITY_UK, SAFE_UK, UK_WARNING, BROKER_WARNING)
    for kind in ("ISA", "GIA")
}


def resolve_instruments(strategy_slug, country, account_type, broker):
    try:
        return SUPPORTED_PROFILES[(strategy_slug, country, account_type, broker)]
    except KeyError as exc:
        raise InstrumentMappingUnavailable(
            "No issuer-verified GBP 9Sig instrument mapping exists yet for this "
            "strategy, country, account type and broker. Nothing was created."
        ) from exc


def for_strategy(strategy):
    """No guesswork for older accounts without recorded broker metadata."""
    try:
        profile = resolve_instruments(
            strategy.preset.slug, strategy.account.country,
            strategy.account.kind, strategy.account.broker)
    except InstrumentMappingUnavailable:
        return None
    # Do not reinterpret previously recorded, unmapped user holdings merely
    # because a shared legacy account later received a broker profile.
    if (strategy.equity_symbol != profile.equity.yahoo_ticker or
            strategy.safe_symbol != profile.safe.yahoo_ticker):
        return None
    return profile
