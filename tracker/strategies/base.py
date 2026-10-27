"""The contract every strategy plug-in fulfils.

Generic code (dashboard, alerts, hourly job, reviews, setup, Study) talks to
a strategy only through this object, found with ``registry.module_for``.
A plug-in's own module may use its strategy's rules; generic code never
imports them directly.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable


# Review note a daily plug-in stamps on an unfilled proposal it replaces; the
# notification job tells the owner such an instruction was withdrawn.
DAILY_SUPERSEDED_NOTE = "Superseded by a later settled daily review before any fills."


_group = dataclass(frozen=True, kw_only=True)


@_group
class Schedule:
    """When reviews happen and how the hourly job refreshes them."""
    # Hourly: refresh this strategy's prices and saved reviews. (instance) -> dict status
    review: Callable
    # The next scheduled review date on or after a day. (date) -> date
    next_review: Callable
    # The last completed review date before a day. (date) -> date
    last_review_before: Callable
    # Daily strategies keep a review actionable for a few sessions: the earliest
    # review date that still counts as current. (day) -> date; None = only the last one.
    current_review_floor: Callable | None = None
    # How often reviews happen, as a word for headings: "quarterly", "yearly".
    cadence_word: str = "quarterly"
    # Market series the rules need besides the held funds (e.g. reference indexes):
    # a tuple, or (instance) -> tuple when it depends on the account.
    reference_symbols: tuple | Callable = ()
    # A legitimate cash regime can have no holdings and still issue reviews.
    allows_cash_only: bool = False


@_group
class Allocation:
    """How the target split is described on the allocation card."""
    # Label for the allocation card's comparison column.
    allocation_label: str = "Target"
    # Target split right now when it isn't fixed weights (e.g. momentum picks one
    # fund): (instance) -> {symbol: weight 0..1}, or None when it can't be computed.
    current_weights: Callable | None = None


@_group
class Dashboard:
    """Optional extra dashboard cards."""
    # Template path and (instance, report, next_end) -> context.
    dashboard_template: str = ""
    dashboard_context: Callable | None = None


@_group
class Resets:
    """Reviews that can carry a reset which sets a new baseline."""
    # Whether reviews can carry a reset (the owner must finish those).
    supports_reset: bool = False
    # The growth share a confirmed reset restores (9Sig 60%, 3Sig 80%), checked within 5 points.
    reset_equity_fraction: Decimal = Decimal("0.60")
    # Whether a saved review may offer a separate manual 60/40 reset review. (signal) -> bool
    offers_manual_reset: Callable = field(default=lambda signal: False)
    # Creates that separate reset review: (owner=, strategy_id=, signal_id=) -> signal
    manual_reset: Callable | None = None


@_group
class ReviewDocs:
    """"How this was calculated" for a saved review, and its CSV. (instance, signal) -> ..."""
    review_detail: Callable | None = None
    review_export: Callable | None = None


@_group
class StudyHooks:
    """The hypothetical Study tab; never touches real data."""
    # None, or an object with the Study API.
    study: object | None = None
    # Hypothetical what-if: (params dict) -> result dict, or raises ValueError
    # for parameters outside the allowed ranges.
    simulate: Callable | None = None
    # Its adjustable inputs: ({"name", "label", "min", "max", "step", "value"}, ...)
    simulate_fields: tuple = ()


@_group
class StrategyModule(Schedule, Allocation, Dashboard, Resets, ReviewDocs, StudyHooks):
    key: str
    # Plug-ins for older rule versions an instance may still be pinned to:
    # {version: StrategyModule}. Instances pinned to a listed version use that
    # plug-in (its schedule, review and Study) until explicitly upgraded.
    by_version: dict = field(default_factory=dict)
