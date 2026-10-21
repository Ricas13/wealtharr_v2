"""Read-only, versioned catalogue for supported UK strategy roles.

The account check is a deliberate hold. Issuer ISA eligibility and a public
Trading 212 Invest listing do not prove this owner's ISA buying permission.
Unreviewed All Weather roles contain NO asserted ticker or ISIN.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import MappingProxyType

from tracker.instrument_mapping import (
    EQUITY_UK, SAFE_UK, MAPPING_VERSION, STRATEGY_9SIG,
    enrolment_manifest,
)
from tracker.strategies.registry import describe_strategy


CATALOGUE_VERSION = MAPPING_VERSION
ALL_WEATHER = "all-weather"
PLATFORMS = (
    ("TRADING212", "Trading 212", True),
    ("AJBELL", "AJ Bell", False),
    ("HL", "Hargreaves Lansdown", False),
    ("VANGUARD", "Vanguard UK", False),
)
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
        ALL_WEATHER, "All Weather", "Moderate risk", "Annual",
        "Five diversifying asset roles; instruments and rules are not released.",
        False,
    ),
}
OFFERS = MappingProxyType(_OFFERS)


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
        }.get(self.role, self.role)

    @property
    def weight_pct(self):
        return self.weight * Decimal("100")


def _reviewed_line(role, item, account_type):
    return CatalogueLine(
        strategy_key=STRATEGY_9SIG,
        role=role,
        country="GB",
        platform="TRADING212",
        account_type=account_type,
        mapping_version=CATALOGUE_VERSION,
        name=item.name,
        ticker=item.yahoo_ticker,
        isin=item.isin,
        raw_quote_unit=item.raw_quote_unit,
        currency=item.currency,
        trading_line=item.trading_line,
        weight=Decimal(item.allocation_pct) / Decimal("100"),
        status="account_check_pending",
        issuer_url=item.issuer_isa_evidence_url or item.issuer_url,
        broker_evidence_url=item.broker_evidence_url,
        verified_on=item.verified_on,
        note=(
            "Issuer and public Trading 212 Invest listing reviewed. Your actual "
            "account permissions and live price quote units are NOT confirmed. "
            "Tracking only until separate confirmation."
        ),
    )


_PLACEHOLDER_ROLES = (
    ("stocks", "Global equity role", ".30"),
    ("long", "Long-term bond role", ".40"),
    ("inter", "Intermediate bond role", ".15"),
    ("gold", "Gold role", ".075"),
    ("comm", "Broad commodity role", ".075"),
)


def _draft_line(role, label, weight, account_type):
    return CatalogueLine(
        ALL_WEATHER, role, "GB", "TRADING212", account_type,
        CATALOGUE_VERSION, label + " — instrument not researched",
        "", "", "", "", "", Decimal(weight), "draft",
        note="Draft role placeholder only. No reviewed security or tradable line.",
    )


_CATALOGUE = {}
for account_type in ("ISA", "GIA"):
    for role, item in (("growth", EQUITY_UK), ("safe", SAFE_UK)):
        row = _reviewed_line(role, item, account_type)
        _CATALOGUE[
            (row.strategy_key, row.role, row.country, row.platform,
             row.account_type, row.mapping_version)
        ] = row
    for role, label, weight in _PLACEHOLDER_ROLES:
        row = _draft_line(role, label, weight, account_type)
        _CATALOGUE[
            (row.strategy_key, row.role, row.country, row.platform,
             row.account_type, row.mapping_version)
        ] = row

CATALOGUE = MappingProxyType(_CATALOGUE)

# Raw Yahoo quote units for reference series that no strategy holds.
REFERENCE_QUOTE_UNITS = {"TQQQ": ("USD",), "SPY": ("USD",), "QQQ": ("USD",)}


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
        offer and offer.enabled and description and platform == "TRADING212"
        and account_type in ("ISA", "GIA")
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
            "raw_to_gbp_factor": "100" if line.raw_quote_unit == "GBX" else "1",
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
    if platform != "TRADING212":
        return "No reviewed mapping for this platform."
    return "No reviewed instruments for this account type. Not available."


def onboarding_manifest(strategy_key, platform, account_type):
    """Return a pinned snapshot; never upgrade a legacy instance implicitly."""
    if not selection_available(strategy_key, platform, account_type):
        raise MappingNotAvailable(availability_reason(strategy_key, platform, account_type))
    if strategy_key != STRATEGY_9SIG:
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
