"""Read-only, versioned catalogue: which exact fund each strategy role uses.

Broker-neutral. A fund *listing* (one exchange trading line: ISIN, ticker,
quote unit) carries its own evidence for each broker known to carry it. A
strategy role names its listings in order of preference; for a broker and
account type the first listing with evidence for that broker is used. No
evidence, no offer: a broker is never assumed to carry a fund.

The account check is a deliberate hold. Issuer ISA eligibility and a public
broker listing do not prove this owner's buying permission.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from types import MappingProxyType

from tracker.core.brokers import BROKERS, broker_name
from tracker.instrument_mapping import (
    EQUITY_UK, SAFE_UK, MAPPING_VERSION, STRATEGY_9SIG,
    enrolment_manifest,
)
from tracker.strategies.registry import describe_strategy


CATALOGUE_VERSION = MAPPING_VERSION
ALL_WEATHER = "all-weather"
GOLDEN_BUTTERFLY = "golden-butterfly"
DUAL_MOMENTUM = "dual-momentum"
ACCOUNT_TYPES = (
    ("ISA", "Stocks & Shares ISA"),
    ("GIA", "General Investment Account"),
    ("SIPP", "SIPP (pension)"),
)


@dataclass(frozen=True)
class StrategyOffer:
    key: str
    title: str
    risk: str
    cadence: str
    explanation: str
    enabled: bool


_OFFERS = {
    STRATEGY_9SIG: StrategyOffer(
        STRATEGY_9SIG, "9Sig", "High risk", "Quarterly",
        "A leveraged Nasdaq-100 growth sleeve with a defensive sleeve. "
        "Quarterly decisions use the versioned 9Sig rules.", True,
    ),
    ALL_WEATHER: StrategyOffer(
        ALL_WEATHER, "All Weather", "Moderate risk", "Yearly",
        "Five funds at the published weights: stocks, long-term and intermediate government "
        "bonds, gold and commodities, rebalanced once a year. Not affiliated with Bridgewater.", True,
    ),
    GOLDEN_BUTTERFLY: StrategyOffer(
        GOLDEN_BUTTERFLY, "Golden Butterfly", "Moderate risk", "Yearly",
        "Five equal parts: stocks, small-cap value, long-term and short-term government bonds, "
        "and gold, rebalanced once a year. Not affiliated with Portfolio Charts.", True,
    ),
    DUAL_MOMENTUM: StrategyOffer(
        DUAL_MOMENTUM, "Dual Momentum", "High risk", "Monthly",
        "100% in one fund, chosen each month: US shares, world ex-US shares, or bonds when shares "
        "aren't beating cash. Not affiliated with Gary Antonacci.", True,
    ),
}
OFFERS = MappingProxyType(_OFFERS)


@dataclass(frozen=True)
class Listing:
    """One exchange trading line of one fund, with per-broker evidence."""
    name: str
    issuer: str
    isin: str
    ticker: str  # Yahoo symbol of this exact line
    raw_quote_unit: str  # GBX (pence), GBP, or USD (converted at that day's GBP/USD close)
    trading_line: str
    issuer_url: str
    note: str
    verified_on: date
    brokers: dict = field(default_factory=dict)  # broker key -> evidence URL
    wrappers: tuple = ("ISA", "GIA")  # account types the issuer says it suits
    country: str = "GB"
    currency: str = "GBP"  # normalised account currency


@dataclass(frozen=True)
class CatalogueLine:
    strategy_key: str
    role: str
    country: str
    platform: str
    account_type: str
    mapping_version: str
    name: str
    ticker: str
    isin: str
    raw_quote_unit: str
    currency: str
    trading_line: str
    weight: Decimal
    status: str  # verified, account_check_pending, draft
    issuer_url: str = ""
    broker_evidence_url: str = ""
    verified_on: date | None = None
    note: str = ""
    issuer: str = ""

    @property
    def role_label(self):
        return {
            "growth": "Growth", "safe": "Safe",
            "stocks": "Equities", "long": "Long bonds",
            "inter": "Intermediate bonds", "gold": "Gold",
            "comm": "Commodities",
            "scv": "Small-cap value", "short": "Short bonds",
    "us": "US shares", "intl": "World ex-US shares", "bonds": "Bonds",
        }.get(self.role, self.role)

    @property
    def weight_pct(self):
        return self.weight * Decimal("100")

    @property
    def broker_name(self):
        return broker_name(self.platform)


ACCOUNT_HOLD = ("Your own account's access and the live price quote unit are NOT confirmed. "
                "A different unit from Yahoo holds the price rather than guessing.")


def _from_instrument(item):
    return Listing(
        item.name, item.issuer, item.isin, item.yahoo_ticker, item.raw_quote_unit,
        item.trading_line, item.issuer_isa_evidence_url or item.issuer_url,
        "Issuer and public broker listing reviewed. " + ACCOUNT_HOLD,
        item.verified_on, brokers={"TRADING212": item.broker_evidence_url})


T212 = "https://www.trading212.com/trading-instruments/invest/%s.GB"
AW_VERIFIED_ON = date(2026, 10, 21)


def _aw(name, issuer, isin, ticker, unit, line, issuer_url, note):
    return Listing(name, issuer, isin, ticker, unit, line, issuer_url,
                   note + " Source-cited, not read first-hand. " + ACCOUNT_HOLD,
                   AW_VERIFIED_ON, brokers={"TRADING212": T212 % ticker.split(".")[0]})


VUAG = _aw("Vanguard S&P 500 UCITS ETF (USD) Accumulating", "Vanguard",
           "IE00BFMXXD54", "VUAG.L", "GBP", "LSE VUAG (GBP line)",
           "https://www.vanguard.co.uk/uk-fund-directory/product/etf/equity/9694/sp-500-ucits-etf-usd-accumulating",
           "US shares in USD, unhedged, on a GBP line.")
GLTL = _aw("SPDR Bloomberg 15+ Year Gilt UCITS ETF (Dist)", "State Street",
           "IE00B6YX5L24", "GLTL.L", "GBP", "LSE GLTL (GBP line)",
           "https://www.ssga.com/uk/en_gb/intermediary/etfs/spdr-bloomberg-15-year-gilt-ucits-etf-dist-sybl-gy",
           "UK equivalent of long-term US Treasuries. Distributing; small fund.")
IGLT = _aw("iShares Core UK Gilts UCITS ETF (Dist)", "iShares",
           "IE00B1FZSB30", "IGLT.L", "GBP", "LSE IGLT (GBP line)",
           "https://www.ishares.com/uk/individual/en/products/251806",
           "UK equivalent of intermediate US Treasuries. All-maturity gilts: no 5-15 year gilt fund "
           "was found at the broker, so it overlaps the long role.")
SGLN = _aw("iShares Physical Gold ETC", "iShares",
           "IE00B4ND3602", "SGLN.L", "GBX", "LSE SGLN (pence line)",
           "https://www.ishares.com/uk/individual/en/products/258441",
           "An ETC (secured debt security), not a UCITS fund; physically backed; unhedged.")
CMOP = _aw("Invesco Bloomberg Commodity UCITS ETF (Acc)", "Invesco",
           "IE00BD6FTQ80", "CMOP.L", "GBX", "LSE CMOP (pence line)",
           "https://www.invesco.com/content/dam/invesco/uk/en/product-documents/etf/share-class/factsheet/IE00BD6FTQ80_factsheet_en-uk.pdf",
           "Swap-based (synthetic); broad commodities including precious metals.")

GB_VERIFIED_ON = date(2026, 10, 21)
USSC = Listing(
    "SPDR MSCI USA Small Cap Value Weighted UCITS ETF (Acc)", "State Street",
    "IE00BSPLC413", "USSC.L", "USD", "LSE USSC (USD line)",
    "https://www.ssga.com/uk/en_gb/intermediary/etfs/funds/spdr-msci-usa-small-cap-value-weighted-ucits-etf-zprv-gy",
    "US small-cap value, the only such index fund found on the London market. Quoted in US dollars: "
    "Wealtharr converts each close at that day's GBP/USD close, and your broker charges its own "
    "currency fee. Record fills in pounds (total paid divided by shares). Source-cited, not read "
    "first-hand. " + ACCOUNT_HOLD,
    GB_VERIFIED_ON, brokers={"TRADING212": T212 % "USSC"})
IGLS = Listing(
    "iShares UK Gilts 0-5yr UCITS ETF (Dist)", "iShares",
    "IE00B4WXJK79", "IGLS.L", "GBP", "LSE IGLS (GBP line)",
    "https://www.ishares.com/gls-download/literature/fact-sheet/igls-ishares-uk-gilts-0-5yr-ucits-etf-fund-fact-sheet-en-gb.pdf",
    "UK equivalent of short-term US Treasuries: gilts up to 5 years. Distributing. Source-cited, "
    "not read first-hand. " + ACCOUNT_HOLD,
    GB_VERIFIED_ON, brokers={"TRADING212": T212 % "IGLS"})

# Dual Momentum. No broker evidence yet: Trading 212 pages for these lines were
# not found, so the strategy isn't offered there until the owner confirms them.
XUSE = Listing(
    "iShares MSCI World ex-USA UCITS ETF (Acc)", "iShares",
    "IE000R4ZNTN3", "XUSE.L", "GBP", "LSE XUSE (GBP line)",
    "https://www.blackrock.com/uk/individual/products/340748/ishares-msci-world-ex-usa-ucits-etf",
    "UK equivalent of MSCI ACWI ex-US: developed markets only (no emerging markets; no ACWI ex-US "
    "UCITS fund was found). Accumulating, so its price is a total return. Listed April 2025. "
    "Source-cited, not read first-hand. " + ACCOUNT_HOLD,
    GB_VERIFIED_ON)
VAGS = Listing(
    "Vanguard Global Aggregate Bond UCITS ETF GBP Hedged (Acc)", "Vanguard",
    "IE00BG47K971", "VAGS.L", "GBP", "LSE VAGS (GBP line)",
    "https://www.vanguard.co.uk/professional/product/etf/bond/9685/global-aggregate-bond-ucits-etf-hedged-accumulating",
    "UK equivalent of US aggregate bonds: global aggregate bonds hedged to pounds. Accumulating. "
    "Source-cited, not read first-hand. " + ACCOUNT_HOLD,
    GB_VERIFIED_ON)

# (strategy, role, country) -> listings, most preferred first.
ROLE_LISTINGS = {
    (STRATEGY_9SIG, "growth", "GB"): (_from_instrument(EQUITY_UK),),
    (STRATEGY_9SIG, "safe", "GB"): (_from_instrument(SAFE_UK),),
    (ALL_WEATHER, "stocks", "GB"): (VUAG,),
    (ALL_WEATHER, "long", "GB"): (GLTL,),
    (ALL_WEATHER, "inter", "GB"): (IGLT,),
    (ALL_WEATHER, "gold", "GB"): (SGLN,),
    (ALL_WEATHER, "comm", "GB"): (CMOP,),
    (GOLDEN_BUTTERFLY, "stocks", "GB"): (VUAG,),
    (GOLDEN_BUTTERFLY, "scv", "GB"): (USSC,),
    (GOLDEN_BUTTERFLY, "long", "GB"): (GLTL,),
    (GOLDEN_BUTTERFLY, "short", "GB"): (IGLS,),
    (GOLDEN_BUTTERFLY, "gold", "GB"): (SGLN,),
    (DUAL_MOMENTUM, "us", "GB"): (VUAG,),
    (DUAL_MOMENTUM, "intl", "GB"): (XUSE,),
    (DUAL_MOMENTUM, "bonds", "GB"): (VAGS,),
}


def _resolve(strategy_key, role, country, broker, account_type):
    for listing in ROLE_LISTINGS.get((strategy_key, role, country), ()):
        if broker.key in listing.brokers and account_type in listing.wrappers:
            return listing
    return None


def _build():
    rows = {}
    for (strategy_key, role, country), _ in ROLE_LISTINGS.items():
        description = describe_strategy(strategy_key)
        weight = dict(description.role_targets).get(role) if description else None
        if weight is None:
            continue
        for broker in BROKERS.values():
            if broker.country != country:
                continue
            for account_type in broker.account_types:
                listing = _resolve(strategy_key, role, country, broker, account_type)
                if not listing:
                    continue
                rows[(strategy_key, role, country, broker.key, account_type, CATALOGUE_VERSION)] = (
                    CatalogueLine(
                        strategy_key, role, country, broker.key, account_type, CATALOGUE_VERSION,
                        listing.name, listing.ticker, listing.isin, listing.raw_quote_unit,
                        listing.currency, listing.trading_line, weight, "account_check_pending",
                        issuer_url=listing.issuer_url, broker_evidence_url=listing.brokers[broker.key],
                        verified_on=listing.verified_on, note=listing.note, issuer=listing.issuer))
    return rows


_CATALOGUE = _build()
CATALOGUE = MappingProxyType(_CATALOGUE)


def platforms():
    """(key, name, has any strategy) for every known broker, for setup."""
    mapped = {key[3] for key in CATALOGUE}
    return tuple((b.key, b.name, b.key in mapped) for b in BROKERS.values())


# Raw Yahoo quote units for reference series that no strategy holds.
REFERENCE_QUOTE_UNITS = {"TQQQ": ("USD",), "SPY": ("USD",), "QQQ": ("USD",), "GBPUSD=X": ("USD",),
                         # Dual Momentum's sterling cash series (Amundi Smart Overnight Return, pence line).
                         "CSH2.L": ("GBp", "GBX")}


def expected_quote_units():
    """Symbol -> accepted raw Yahoo currency codes, from every catalogued fund.

    A GBX line accepts Yahoo's "GBp"/"GBX"; a GBP line only "GBP". A unit
    change on the feed then holds the price instead of valuing at 100x.
    """
    units = dict(REFERENCE_QUOTE_UNITS)
    for line in CATALOGUE.values():
        if line.ticker and line.raw_quote_unit:
            units[line.ticker] = (("GBp", "GBX") if line.raw_quote_unit == "GBX"
                                  else (line.raw_quote_unit,))
    return units


class MappingNotAvailable(ValueError):
    pass


def rows_for(strategy_key, platform, account_type, *, country="GB",
             version=CATALOGUE_VERSION):
    if version != CATALOGUE_VERSION:
        raise MappingNotAvailable("Mapping version is not released.")
    rows = tuple(
        line for key, line in CATALOGUE.items()
        if key[0] == strategy_key and key[2] == country
        and key[3] == platform and key[4] == account_type
        and key[5] == version
    )
    return rows


def selection_available(strategy_key, platform, account_type):
    offer = OFFERS.get(strategy_key)
    description = describe_strategy(strategy_key)
    rows = rows_for(strategy_key, platform, account_type)
    return bool(
        offer and offer.enabled and description and platform in BROKERS
        # One reviewed line for every role the strategy's rules define.
        and {row.role for row in rows} == {role for role, _ in description.role_targets}
        and all(row.status != "draft" and row.ticker and row.isin for row in rows)
    )


def _manifest_from_lines(strategy_key, platform, account_type):
    """Pinned snapshot for a catalogue-defined strategy (any number of roles)."""
    import hashlib
    import json
    roles = {}
    for line in rows_for(strategy_key, platform, account_type):
        roles[line.role] = {
            "name": line.name, "issuer": line.issuer, "isin": line.isin,
            "broker_ticker": line.ticker.split(".")[0], "yahoo_ticker": line.ticker,
            "trading_line": line.trading_line, "raw_quote_unit": line.raw_quote_unit,
            "normalised_currency": line.currency,
            "raw_to_gbp_factor": {"GBX": "100", "GBP": "1"}.get(line.raw_quote_unit, "GBPUSD=X daily close"),
            "weight": str(line.weight),
            "broker_isa_confirmed": False, "owner_product_permission_confirmed": False,
            "issuer_url": line.issuer_url, "broker_evidence_url": line.broker_evidence_url,
        }
    manifest = {
        "strategy_key": strategy_key, "mapping_version": CATALOGUE_VERSION,
        "country": "GB", "broker": platform, "account_type": account_type, "roles": roles,
    }
    serial = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    manifest["sha256"] = hashlib.sha256(serial.encode()).hexdigest()
    return manifest


def availability_reason(strategy_key, platform, account_type):
    if selection_available(strategy_key, platform, account_type):
        return ""
    if strategy_key not in OFFERS or not OFFERS[strategy_key].enabled:
        return "This strategy is not released yet."
    if not any(key[0] == strategy_key and key[3] == platform for key in CATALOGUE):
        return "No reviewed funds for this strategy at this broker yet."
    return "No reviewed instruments for this account type. Not available."


def onboarding_manifest(strategy_key, platform, account_type):
    """Return a pinned snapshot; never upgrade a legacy instance implicitly."""
    if not selection_available(strategy_key, platform, account_type):
        raise MappingNotAvailable(availability_reason(strategy_key, platform, account_type))
    if strategy_key != STRATEGY_9SIG or platform != "TRADING212":
        return _manifest_from_lines(strategy_key, platform, account_type)
    # 9Sig keeps its original manifest so existing pinned snapshots still match.
    if account_type == "ISA":
        return enrolment_manifest(
            strategy_key, country="GB", broker="TRADING212",
            account_type="ISA",
        )
    # GIA uses the same exact identified trading lines, not an inferred
    # substitute. Personal dealing permission is still not confirmed.
    import hashlib
    import json
    manifest = enrolment_manifest(
        strategy_key, country="GB", broker="TRADING212",
        account_type="ISA",
    ).copy()
    manifest["account_type"] = "GIA"
    manifest.pop("sha256")
    serial = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    manifest["sha256"] = hashlib.sha256(serial.encode()).hexdigest()
    return manifest


def released_version(strategy_key):
    description = describe_strategy(strategy_key)
    if description is None:
        raise MappingNotAvailable("No released strategy rule version.")
    return description.version
