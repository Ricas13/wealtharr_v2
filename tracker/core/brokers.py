"""Brokers Wealtharr knows about. Tracking only: nothing here connects to one.

A broker is a name, a country and the account types it offers. Which funds
it can hold comes from the evidence on each fund listing in the catalogue,
never from the broker entry, so adding a broker here offers nothing until a
listing cites evidence that the broker carries it.
"""
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class Broker:
    key: str
    name: str
    country: str
    account_types: tuple
    currency: str = "GBP"  # the account's currency: every amount is tracked in it


_BROKERS = {
    "TRADING212": Broker("TRADING212", "Trading 212", "GB", ("ISA", "GIA")),
    "AJBELL": Broker("AJBELL", "AJ Bell", "GB", ("ISA", "GIA", "SIPP")),
    "HL": Broker("HL", "Hargreaves Lansdown", "GB", ("ISA", "GIA", "SIPP")),
    "IBKR_UK": Broker("IBKR_UK", "Interactive Brokers UK", "GB", ("ISA", "GIA", "SIPP")),
    "FIDELITY": Broker("FIDELITY", "Fidelity", "US", ("BROKERAGE", "TRAD_IRA", "ROTH_IRA"), "USD"),
    "TRADE_REPUBLIC": Broker("TRADE_REPUBLIC", "Trade Republic", "DE", ("DEPOT",), "EUR"),
}
BROKERS = MappingProxyType(_BROKERS)


# Every account type any broker offers, in display order.
ACCOUNT_TYPE_NAMES = {
    "ISA": "Stocks & Shares ISA", "GIA": "General Investment Account", "SIPP": "SIPP (pension)",
    "BROKERAGE": "Individual brokerage account", "TRAD_IRA": "Traditional IRA", "ROTH_IRA": "Roth IRA",
    "DEPOT": "Securities account (Depot)",
}
CURRENCY_SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}


def broker_name(key):
    broker = BROKERS.get(key)
    return broker.name if broker else key
