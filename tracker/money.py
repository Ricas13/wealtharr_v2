"""The currency symbol for the page being rendered.

Each account is tracked in one currency (pounds, dollars or euros). A page
about one strategy shows that account's symbol; ``rendering_in`` sets it for
the duration of a render so the money filters pick it up.
"""
from contextlib import contextmanager
from contextvars import ContextVar

from .core.brokers import CURRENCY_SYMBOLS

_symbol = ContextVar("money_symbol", default="£")


def symbol_for(currency):
    return CURRENCY_SYMBOLS.get(currency or "GBP", (currency or "") + " ")


def current_symbol():
    return _symbol.get()


@contextmanager
def rendering_in(currency):
    token = _symbol.set(symbol_for(currency))
    try:
        yield
    finally:
        _symbol.reset(token)
