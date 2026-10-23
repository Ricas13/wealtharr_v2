"""The contract every strategy plug-in fulfils.

Generic code (dashboard, alerts, hourly job, reviews, setup, Study) talks to
a strategy only through this object, found with ``registry.module_for``.
A plug-in's own module may use its strategy's rules; generic code never
imports them directly.
"""
from dataclasses import dataclass, field
from decimal import Decimal
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
    # Daily strategies keep a review actionable for a few sessions: the earliest
    # review date that still counts as current. (day) -> date; None = only the last one.
    current_review_floor: Callable | None = None
    # Market series the rules need besides the held funds (e.g. reference indexes):
    # a tuple, or (instance) -> tuple when it depends on the account.
    reference_symbols: tuple | Callable = ()
    # Optional extra dashboard cards: template path and (instance, report, next_end) -> context.
    dashboard_template: str = ""
    dashboard_context: Callable | None = None
    # How often reviews happen, as a word for headings: "quarterly", "yearly".
    cadence_word: str = "quarterly"
    # Label for the allocation card's comparison column.
    allocation_label: str = "Target"
    # Whether reviews can carry a reset that sets a new baseline (owner must finish those).
    supports_reset: bool = False
    # The growth share a confirmed reset restores (9Sig 60%, 3Sig 80%), checked within 5 points.
    reset_equity_fraction: Decimal = Decimal("0.60")
    # Whether a saved review may offer a separate manual 60/40 reset review. (signal) -> bool
    offers_manual_reset: Callable = field(default=lambda signal: False)
    # Creates that separate reset review: (owner=, strategy_id=, signal_id=) -> signal
    manual_reset: Callable | None = None
    # A legitimate cash regime can have no holdings and still issue reviews.
    allows_cash_only: bool = False
    # Target split right now when it isn't fixed weights (e.g. momentum picks one
    # fund): (instance) -> {symbol: weight 0..1}, or None when it can't be computed.
    current_weights: Callable | None = None
    # Hypothetical Study support: None, or an object with the Study API.
    study: object | None = None
    # "How this was calculated" for a saved review, and its CSV. (instance, signal) -> ...
    review_detail: Callable | None = None
    review_export: Callable | None = None
    # Plug-ins for older rule versions an instance may still be pinned to:
    # {version: StrategyModule}. Instances pinned to a listed version use that
    # plug-in (its schedule, review and Study) until explicitly upgraded.
    by_version: dict = field(default_factory=dict)
