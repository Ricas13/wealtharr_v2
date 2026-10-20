"""Evidence-backed owner tracking lines, keyed by strategy, country, wrapper and broker.

This registry is deliberately small. Unsupported combinations fail closed and
never silently substitute a security with another ISIN or price currency.
'Broker listed' evidence is for Trading 212 INVEST; individual Stocks ISA
availability and appropriateness are NOT guaranteed.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import MappingProxyType

# Released selections are immutable. Change mapping identity => a new version.
MAPPING_VERSION = "2026-10-19.1"
RESEARCH_VERIFIED_ON = date(2026, 10, 19)

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
    # Section B: evidence and quote units. The legacy 'currency' is the
    # normalised account currency (GBP), never the raw vendor quote unit.
    role: str = ""
    issuer: str = ""
    trading_line: str = ""
    raw_quote_unit: str = ""     # GBX (pence) or GBP (pounds); explicit
    raw_to_gbp_factor: Decimal = Decimal("1")
    structure: str = ""
    leverage_reset: str = ""
    income_policy: str = ""
    verified_on: date | None = None
    issuer_isa_evidence_url: str = ""
    issuer_says_isa_eligible: bool = False
    broker_invest_listed: bool = False
    broker_isa_confirmed: bool = False
    owner_product_permission_confirmed: bool = False
    yahoo_price_unit_confirmed: bool = False
    evidence_note: str = ""

    @property
    def isa_access_requires_owner_check(self):
        # Public issuer/Invest pages do not prove access in a specific ISA.
        return not (self.broker_isa_confirmed and self.owner_product_permission_confirmed)


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
    mapping_version: str = MAPPING_VERSION

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
    role="growth",
    issuer="WisdomTree Multi Asset Issuer PLC",
    trading_line="LQQ3 / LSE / GBX / SEDOL BKSB2C0",
    raw_quote_unit="GBX",
    raw_to_gbp_factor=Decimal("0.01"),
    structure="Collateralised swap-backed debt security ETP; not a UCITS ETF",
    leverage_reset="3x daily, intraday restrike threshold 17%; severe overnight gap threshold 25%",
    income_policy="No cash distribution expected; check all corporate actions",
    verified_on=RESEARCH_VERIFIED_ON,
    issuer_isa_evidence_url="https://www.wisdomtree.eu/sv-se/etps/equities/wisdomtree-nasdaq-100-3x-daily-leveraged",
    issuer_says_isa_eligible=True,
    broker_invest_listed=True,
    broker_isa_confirmed=False,
    owner_product_permission_confirmed=False,
    yahoo_price_unit_confirmed=False,
    evidence_note=("Issuer verifies ISIN/line and ISA eligibility, and Trading 212 lists it in Invest. "
                   "Personal Trading 212 Stocks ISA availability, appropriateness approval, "
                   "fractional trading and Yahoo raw quote metadata remain unverified."),
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
    role="safe",
    issuer="iShares IV plc (BlackRock)",
    trading_line="ERNS / LSE / GBP / SEDOL BCRY644",
    raw_quote_unit="GBP",
    raw_to_gbp_factor=Decimal("1"),
    structure="Physical sampled UCITS ETF holding ultrashort GBP investment-grade debt",
    leverage_reset="None (unleveraged)",
    income_policy="Distributing, normally quarterly: actual dividends must be in the ledger",
    verified_on=RESEARCH_VERIFIED_ON,
    issuer_isa_evidence_url="https://www.ishares.com/uk/individual/en/products/258120/ishares-ultrashort-bond-ucits-etf",
    issuer_says_isa_eligible=True,
    broker_invest_listed=True,
    broker_isa_confirmed=False,
    owner_product_permission_confirmed=False,
    yahoo_price_unit_confirmed=False,
    evidence_note=("Issuer verifies ISIN/GBP line and ISA eligibility, and Trading 212 lists it in Invest. "
                   "Personal Stocks ISA availability and live Yahoo quote metadata remain unverified. "
                   "Its dividend payments affect cash/performance and cannot be silently ignored."),
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

# The SECTION B CURATED MAP contains only the first supported onboarding scope:
# UK / Trading 212 / Stocks & Shares ISA. It is keyed by
# (strategy, role, country, platform, account_type, mapping_version).
# Issuer eligibility + Invest listing are evidence; they DO NOT assert a
# particular owner's Stocks ISA can actually buy the instrument.
CURATED_ISA_ROLE_MAP = MappingProxyType({
    (STRATEGY_9SIG, "growth", "GB", "TRADING212", "ISA", MAPPING_VERSION): EQUITY_UK,
    (STRATEGY_9SIG, "safe", "GB", "TRADING212", "ISA", MAPPING_VERSION): SAFE_UK,
})


def resolve_curated_isa_role(strategy_slug, role, country, broker, account_type,
                             mapping_version=MAPPING_VERSION):
    """Resolve an exact fixed line without runtime ticker guessing."""
    try:
        return CURATED_ISA_ROLE_MAP[
            (strategy_slug, role, country, broker, account_type, mapping_version)
        ]
    except KeyError as exc:
        raise InstrumentMappingUnavailable(
            "This strategy role has no reviewed instrument mapping for the "
            "selected broker, account type and mapping version."
        ) from exc


# Existing PRE-REFACTOR callers still use this profile table; its GIA entry is
# legacy compatibility only, NOT an offer to new users. Section F will remove
# legacy GIA onboarding after the one-screen ISA flow and tests are migrated.
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


def for_historical_import(strategy):
    """A pinned, exact ISIN map for evidence from REAL past broker fills.

    This does not approve new trades. A v2 tracking-only instance keeps
    for_strategy() returning None for review execution and notifications.
    """
    if not getattr(strategy, "strategy_key", ""):
        return for_strategy(strategy)
    if (not strategy.tracking_only or not strategy.owner_checked_on or
            strategy.strategy_key != strategy.preset.slug or
            strategy.strategy_key != STRATEGY_9SIG or
            strategy.account.owner_id != strategy.owner_id or
            strategy.platform_code != strategy.account.broker or
            strategy.account_type_code != strategy.account.kind or
            strategy.mapping_version != MAPPING_VERSION):
        return None
    from .core.catalogue import onboarding_manifest, selection_available
    if not selection_available(
            strategy.strategy_key, strategy.platform_code, strategy.account_type_code):
        return None
    if strategy.resolved_instruments != onboarding_manifest(
            strategy.strategy_key, strategy.platform_code, strategy.account_type_code):
        return None
    return resolve_instruments(
        strategy.strategy_key, strategy.account.country,
        strategy.account.kind, strategy.account.broker)


def for_strategy(strategy):
    """No guesswork for older accounts without recorded broker metadata."""
    # New v2 enrolments explicitly remain tracking-only while instrument,
    # owner account eligibility and quote-unit checks are outstanding.
    # Old instances default False, so established legacy behaviour is unchanged.
    if getattr(strategy, "tracking_only", False):
        return None
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
    # New enrolments carry an immutable-at-creation manifest. Fail closed if
    # runtime mapping rules changed or the stored exact-line snapshot differs.
    # Older instances without a manifest retain the pre-F legacy pathway;
    # migrate them only via a separately approved, audited upgrade.
    snapshot = strategy.resolved_instruments or {}
    if snapshot:
        if (not isinstance(snapshot, dict) or
                strategy.mapping_version != snapshot.get("mapping_version") or
                strategy.mapping_version != MAPPING_VERSION):
            return None
        try:
            released = enrolment_manifest(
                strategy.preset.slug, country=strategy.account.country,
                account_type=strategy.account.kind,
                broker=strategy.account.broker)
        except InstrumentMappingUnavailable:
            return None
        if snapshot != released:
            return None
    return profile


def enrolment_manifest(strategy_slug=STRATEGY_9SIG, *, country="GB",
                       broker="TRADING212", account_type="ISA"):
    """Capture only reviewed, exact ISA trading lines as an immutable JSON value.

    An enrolment records the CURRENT code-defined identity and evidence
    qualifiers. It never asserts personal broker permission or changes the
    currently supported account type. Historic instances retain their
    snapshots even if the code-defined catalogue is later revised.
    """
    import hashlib
    import json

    roles = {}
    for role in ("growth", "safe"):
        instrument = resolve_curated_isa_role(
            strategy_slug, role, country, broker, account_type,
            mapping_version=MAPPING_VERSION)
        roles[role] = {
            "name": instrument.name,
            "issuer": instrument.issuer,
            "isin": instrument.isin,
            "broker_ticker": instrument.broker_ticker,
            "yahoo_ticker": instrument.yahoo_ticker,
            "trading_line": instrument.trading_line,
            "raw_quote_unit": instrument.raw_quote_unit,
            "normalised_currency": instrument.currency,
            "raw_to_gbp_factor": str(instrument.raw_to_gbp_factor),
            "issuer_says_isa_eligible": instrument.issuer_says_isa_eligible,
            "broker_invest_listed": instrument.broker_invest_listed,
            "broker_isa_confirmed": instrument.broker_isa_confirmed,
            "owner_product_permission_confirmed": instrument.owner_product_permission_confirmed,
            "yahoo_price_unit_confirmed": instrument.yahoo_price_unit_confirmed,
            "issuer_url": instrument.issuer_url,
            "broker_evidence_url": instrument.broker_evidence_url,
        }
    manifest = {
        "strategy_key": strategy_slug, "mapping_version": MAPPING_VERSION,
        "country": country, "broker": broker, "account_type": account_type,
        "roles": roles,
    }
    serialised = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    manifest["sha256"] = hashlib.sha256(serialised.encode("utf-8")).hexdigest()
    return manifest
