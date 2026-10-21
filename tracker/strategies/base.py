"""The contract every strategy plug-in fulfils.

Generic code (dashboard, alerts, hourly job, reviews, setup, Study) talks to
a strategy only through this object, found with ``registry.module_for``.
A plug-in's own module may use its strategy's rules; generic code never
imports them directly.
"""
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class StrategyModule:
    key: str
    # Hourly: refresh this strategy's prices and saved reviews. (instance) -> dict status
    review: Callable
    # The next scheduled review date on or after a day. (date) -> date
    next_review: Callable
    # The last completed review date before a day. (date) -> date
    last_review_before: Callable
    # Market series the rules need besides the held funds (e.g. reference indexes).
    reference_symbols: tuple = ()
    # Optional extra dashboard cards: template path and (instance, report, next_end) -> context.
    dashboard_template: str = ""
    dashboard_context: Callable | None = None
    # Label for the allocation card's comparison column.
    allocation_label: str = "Target"
    # Whether reviews can carry a reset that sets a new baseline (owner must finish those).
    supports_reset: bool = False
    # Whether a saved review may offer a separate manual 60/40 reset review. (signal) -> bool
    offers_manual_reset: Callable = field(default=lambda signal: False)
    # Creates that separate reset review: (owner=, strategy_id=, signal_id=) -> signal
    manual_reset: Callable | None = None
    # Hypothetical Study support: None, or an object with the Study API.
    study: object | None = None
    # "How this was calculated" for a saved review, and its CSV. (instance, signal) -> ...
    review_detail: Callable | None = None
    review_export: Callable | None = None
