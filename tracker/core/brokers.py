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


_BROKERS = {
    "TRADING212": Broker("TRADING212", "Trading 212", "GB", ("ISA", "GIA")),
    "AJBELL": Broker("AJBELL", "AJ Bell", "GB", ("ISA", "GIA", "SIPP")),
    "HL": Broker("HL", "Hargreaves Lansdown", "GB", ("ISA", "GIA", "SIPP")),
    "IBKR_UK": Broker("IBKR_UK", "Interactive Brokers UK", "GB", ("ISA", "GIA", "SIPP")),
}
BROKERS = MappingProxyType(_BROKERS)


def broker_name(key):
    broker = BROKERS.get(key)
    return broker.name if broker else key
